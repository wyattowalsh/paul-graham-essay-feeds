"""Unit tests for .github/scripts/product_identity.py (offline, git fixtures)."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

import pytest

_HELPER = Path(__file__).resolve().parents[2] / ".github" / "scripts" / "product_identity.py"
_spec = importlib.util.spec_from_file_location("product_identity_under_test", _HELPER)
if _spec is None or _spec.loader is None:
    msg = f"unable to load product identity helper from {_HELPER}"
    raise RuntimeError(msg)
pi = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = pi
_spec.loader.exec_module(pi)

RUN_ID = 123456789
RUN_ATTEMPT = 1
REST_UPDATED = pi.RestMetadata(RUN_ID, RUN_ATTEMPT, pi.REPOSITORY, "updated")
REST_UNCHANGED = pi.RestMetadata(RUN_ID, RUN_ATTEMPT, pi.REPOSITORY, "unchanged")

BASE_FILES: dict[str, bytes] = {
    "catalog.json": b'{"n":0}',
    "feeds/rss.xml": b"<rss v1/>",
    "feeds/atom.xml": b"<atom v1/>",
    "feeds/feed.json": b'{"feed":1}',
    "feeds/rss.simple.xml": b"<rss s1/>",
    "feeds/atom.simple.xml": b"<atom s1/>",
    "feeds/feed.simple.json": b'{"feed":"s1"}',
}

_SHA1 = "c" * 40
_OTHER_SHA1 = "d" * 40
_DIGEST = pi.candidate_digest(BASE_FILES)


# --- git fixture helpers -------------------------------------------------------


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "commit.gpgSign=false", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=check,
    )


def _write(repo: Path, rel: str, data: bytes) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _commit(
    repo: Path,
    *paragraphs: str,
    changes: dict[str, bytes | None] | None = None,
) -> str:
    if changes:
        for rel, value in changes.items():
            if value is None:
                (repo / rel).unlink()
            else:
                _write(repo, rel, value)
    _git(repo, "add", "-A")
    args = ["commit", "-q", "--allow-empty", "-m", paragraphs[0]]
    for extra in paragraphs[1:]:
        args += ["-m", extra]
    _git(repo, *args)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def make_repo(tmp_path: Path, name: str = "r", object_format: str = "sha1") -> Path:
    repo = tmp_path / name
    repo.mkdir(parents=True)
    subprocess.run(
        [
            "git",
            "init",
            "-q",
            "-b",
            "main",
            "--object-format",
            object_format,
            str(repo),
        ],
        check=True,
        capture_output=True,
    )
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    return repo


def make_base_repo(
    tmp_path: Path, name: str = "r", object_format: str = "sha1"
) -> tuple[Path, str]:
    repo = make_repo(tmp_path, name=name, object_format=object_format)
    source = _commit(repo, "base", changes=dict(BASE_FILES))
    return repo, source


def make_origin(tmp_path: Path, repo: Path, refs: dict[str, str]) -> tuple[Path, Path]:
    """Create a bare origin holding ``name -> oid`` refs plus an empty consumer clone."""
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True, capture_output=True)
    for name, oid in refs.items():
        _git(repo, "push", "-q", str(bare), f"{oid}:refs/heads/{name}")
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(consumer)], check=True, capture_output=True
    )
    _git(consumer, "remote", "add", "origin", str(bare))
    return bare, consumer


def update_rest(
    action: str, run_id: int = RUN_ID, run_attempt: int = RUN_ATTEMPT
) -> pi.RestMetadata:
    return pi.RestMetadata(run_id, run_attempt, pi.REPOSITORY, action)


def valid_doc(action: str = "updated") -> dict[str, Any]:
    committed = action != "unchanged"
    sha = _SHA1 if committed else _OTHER_SHA1
    return {
        "action": action,
        "candidate_digest": _DIGEST,
        "committed": committed,
        "product_sha": sha,
        "published": False,
        "repository": pi.REPOSITORY,
        "schema_version": 1,
        "source_sha": _OTHER_SHA1,
        "subjects": list(pi.PRODUCT_SUBJECTS),
        "workflow_run_attempt": RUN_ATTEMPT,
        "workflow_run_id": RUN_ID,
    }


def doc_bytes(payload: dict[str, Any]) -> bytes:
    return pi.canonical_json_bytes(payload)


def rejects(data: bytes, rest: pi.RestMetadata, code: str, **kwargs: Any) -> None:
    with pytest.raises(pi.ProductIdentityError) as excinfo:
        pi.validate_identity_document(data, rest, **kwargs)
    assert excinfo.value.code == code, f"{excinfo.value}"


def make_tree(tmp_path: Path, files: dict[str, bytes]) -> Path:
    root = tmp_path / "candidate"
    root.mkdir()
    for rel, data in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


def tree_root(tmp_path: Path) -> Path:
    return make_tree(tmp_path, dict(BASE_FILES))


# --- A. canonical JSON and identity document -----------------------------------


class TestCanonicalJson:
    def test_canonical_text_shape(self) -> None:
        text = pi.canonical_json_text({"b": 1, "a": 2})
        assert text == '{\n  "a": 2,\n  "b": 1\n}\n'

    def test_canonical_ensure_ascii_false(self) -> None:
        assert "é" in pi.canonical_json_text({"k": "é"})

    def test_parse_rejects_bom(self) -> None:
        with pytest.raises(pi.ProductIdentityError):
            pi.parse_strict_json(b"\xef\xbb\xbf{}")

    def test_parse_rejects_invalid_utf8(self) -> None:
        with pytest.raises(pi.ProductIdentityError):
            pi.parse_strict_json(b'{"a": "\xff"}')

    def test_parse_rejects_duplicate_keys(self) -> None:
        with pytest.raises(pi.ProductIdentityError):
            pi.parse_strict_json(b'{"a": 1, "a": 2}')

    def test_parse_rejects_nested_duplicate_keys(self) -> None:
        with pytest.raises(pi.ProductIdentityError):
            pi.parse_strict_json(b'{"x": {"a": 1, "a": 2}}')

    @pytest.mark.parametrize("constant", [b"NaN", b"Infinity", b"-Infinity"])
    def test_parse_rejects_non_finite(self, constant: bytes) -> None:
        with pytest.raises(pi.ProductIdentityError):
            pi.parse_strict_json(b'{"v": ' + constant + b"}")

    def test_parse_rejects_trailing_json(self) -> None:
        with pytest.raises(pi.ProductIdentityError):
            pi.parse_strict_json(b'{"a": 1} {"b": 2}')

    def test_parse_rejects_trailing_garbage(self) -> None:
        with pytest.raises(pi.ProductIdentityError):
            pi.parse_strict_json(b'{"a": 1}null')

    def test_parse_rejects_empty_and_whitespace(self) -> None:
        with pytest.raises(pi.ProductIdentityError):
            pi.parse_strict_json(b"")
        with pytest.raises(pi.ProductIdentityError):
            pi.parse_strict_json(b"   \n")


class TestIdentityDocument:
    def test_valid_document_roundtrip(self) -> None:
        identity = pi.validate_identity_document(doc_bytes(valid_doc()), REST_UPDATED)
        assert identity.action == "updated"
        assert identity.subjects == pi.PRODUCT_SUBJECTS
        assert identity.to_bytes() == doc_bytes(valid_doc())

    def test_golden_bytes(self) -> None:
        data = pi.build_identity_document(
            action="updated",
            candidate_digest=pi.candidate_digest(),
            committed=True,
            product_sha=_SHA1,
            repository=pi.REPOSITORY,
            source_sha=_OTHER_SHA1,
            workflow_run_attempt=RUN_ATTEMPT,
            workflow_run_id=RUN_ID,
            origin_main=_SHA1,
            object_format="sha1",
        )
        assert data.endswith(b"\n")
        text = data.decode("utf-8")
        assert '"schema_version": 1' in text
        assert json.loads(text)["published"] is True
        lines = text.splitlines()
        assert lines[0] == "{"
        assert lines[-1] == "}"
        keys = [ln.strip().split(":")[0].strip('"') for ln in lines if ln.startswith('  "')]
        assert keys == sorted(keys)
        assert set(keys) == set(pi.IDENTITY_KEYS)

    def test_size_ceiling_enforced(self) -> None:
        payload = valid_doc()
        payload["workflow_run_id"] = int("9" * (pi.MAX_ARTIFACT_BYTES))
        assert len(doc_bytes(payload)) > pi.MAX_ARTIFACT_BYTES
        rejects(
            doc_bytes(payload),
            update_rest("updated", run_id=payload["workflow_run_id"]),
            "artifact_too_large",
        )

    def test_missing_key(self) -> None:
        payload = valid_doc()
        del payload["repository"]
        rejects(doc_bytes(payload), REST_UPDATED, "key_set_mismatch")

    def test_extra_key(self) -> None:
        payload = valid_doc()
        payload["observed_main_sha"] = _SHA1
        rejects(doc_bytes(payload), REST_UPDATED, "key_set_mismatch")

    def test_non_object_payload(self) -> None:
        rejects(pi.canonical_json_bytes([1, 2]), REST_UPDATED, "json_not_object")

    def test_compact_bytes_rejected(self) -> None:
        payload = valid_doc()
        rejects(json.dumps(payload, sort_keys=True).encode(), REST_UPDATED, "noncanonical_bytes")

    def test_four_space_indent_rejected(self) -> None:
        payload = valid_doc()
        rejects(
            (json.dumps(payload, indent=4, sort_keys=True) + "\n").encode(),
            REST_UPDATED,
            "noncanonical_bytes",
        )

    def test_missing_final_lf_rejected(self) -> None:
        payload = valid_doc()
        rejects(
            json.dumps(payload, indent=2, sort_keys=True).encode(),
            REST_UPDATED,
            "noncanonical_bytes",
        )

    def test_crlf_rejected(self) -> None:
        payload = valid_doc()
        raw = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        rejects(raw.replace("\n", "\r\n").encode(), REST_UPDATED, "noncanonical_bytes")

    def test_unsorted_keys_rejected(self) -> None:
        payload = valid_doc()
        items = list(payload.items())
        unsorted = dict(reversed(items))
        rejects(
            (json.dumps(unsorted, indent=2) + "\n").encode("utf-8"),
            REST_UPDATED,
            "noncanonical_bytes",
        )

    @pytest.mark.parametrize(
        ("field", "value", "code"),
        [
            ("schema_version", True, "wrong_type"),
            ("schema_version", 1.0, "wrong_type"),
            ("workflow_run_id", True, "wrong_type"),
            ("workflow_run_id", 1.0, "wrong_type"),
            ("workflow_run_id", 0, "invalid_value"),
            ("workflow_run_id", -1, "invalid_value"),
            ("workflow_run_attempt", True, "wrong_type"),
            ("workflow_run_attempt", 0, "invalid_value"),
            ("committed", 1, "wrong_type"),
            ("committed", "true", "wrong_type"),
            ("published", 1, "wrong_type"),
            ("published", "true", "wrong_type"),
        ],
    )
    def test_wrong_scalar_types(self, field: str, value: Any, code: str) -> None:
        payload = valid_doc()
        payload[field] = value
        rejects(doc_bytes(payload), REST_UPDATED, code)

    def test_schema_version_two_rejected(self) -> None:
        payload = valid_doc()
        payload["schema_version"] = 2
        rejects(doc_bytes(payload), REST_UPDATED, "invalid_value")

    def test_run_identity_mismatch(self) -> None:
        payload = valid_doc()
        payload["workflow_run_id"] = RUN_ID + 1
        rejects(doc_bytes(payload), REST_UPDATED, "rest_metadata_mismatch")

    def test_run_attempt_mismatch(self) -> None:
        payload = valid_doc()
        payload["workflow_run_attempt"] = 2
        rejects(doc_bytes(payload), REST_UPDATED, "rest_metadata_mismatch")

    def test_action_not_allowed(self) -> None:
        payload = valid_doc()
        payload["action"] = "deleted"
        rejects(doc_bytes(payload), REST_UPDATED, "invalid_action")

    def test_action_mismatch_with_rest(self) -> None:
        payload = valid_doc()
        rejects(doc_bytes(payload), REST_UNCHANGED, "rest_metadata_mismatch")

    def test_repository_mismatch(self) -> None:
        payload = valid_doc()
        payload["repository"] = "someone/else"
        rejects(doc_bytes(payload), REST_UPDATED, "rest_metadata_mismatch")

    @pytest.mark.parametrize(
        "digest",
        [
            "sha256:" + "A" * 64,
            "sha256:" + "0" * 63,
            "sha256:" + "0" * 65,
            "0" * 64,
            "sha1:" + "0" * 64,
            "",
            42,
            "sha256:" + "g" * 64,
        ],
    )
    def test_digest_malformed(self, digest: Any) -> None:
        payload = valid_doc()
        payload["candidate_digest"] = digest
        rejects(doc_bytes(payload), REST_UPDATED, "invalid_digest")

    def test_subjects_wrong_order(self) -> None:
        payload = valid_doc()
        subjects = list(pi.PRODUCT_SUBJECTS)
        subjects[0], subjects[1] = subjects[1], subjects[0]
        payload["subjects"] = subjects
        rejects(doc_bytes(payload), REST_UPDATED, "invalid_subjects")

    def test_subjects_missing_one(self) -> None:
        payload = valid_doc()
        payload["subjects"] = list(pi.PRODUCT_SUBJECTS)[:-1]
        rejects(doc_bytes(payload), REST_UPDATED, "invalid_subjects")

    def test_subjects_extra_one(self) -> None:
        payload = valid_doc()
        payload["subjects"] = [*pi.PRODUCT_SUBJECTS, "feeds/extra.xml"]
        rejects(doc_bytes(payload), REST_UPDATED, "invalid_subjects")

    def test_subjects_case_variant(self) -> None:
        payload = valid_doc()
        payload["subjects"] = [
            "feeds/RSS.xml" if s == "feeds/rss.xml" else s for s in pi.PRODUCT_SUBJECTS
        ]
        rejects(doc_bytes(payload), REST_UPDATED, "invalid_subjects")

    def test_oids_validated_against_object_format(self) -> None:
        rejects(doc_bytes(valid_doc()), REST_UPDATED, "invalid_oid", object_format="sha256")

    def test_oids_accepted_for_matching_format(self) -> None:
        identity = pi.validate_identity_document(
            doc_bytes(valid_doc()), REST_UPDATED, object_format="sha1"
        )
        assert identity.product_sha == _SHA1

    def test_unchanged_consistency(self) -> None:
        payload = valid_doc("unchanged")
        identity = pi.validate_identity_document(doc_bytes(payload), REST_UNCHANGED)
        assert identity.committed is False
        assert identity.product_sha == identity.source_sha

    def test_unchanged_with_committed_true_rejected(self) -> None:
        payload = valid_doc()
        payload["action"] = "unchanged"
        payload["committed"] = True
        rejects(doc_bytes(payload), REST_UNCHANGED, "action_inconsistent")

    def test_unchanged_with_distinct_shas_rejected(self) -> None:
        payload = valid_doc()
        payload["action"] = "unchanged"
        payload["committed"] = False
        rejects(doc_bytes(payload), REST_UNCHANGED, "action_inconsistent")

    def test_updated_with_committed_false_rejected(self) -> None:
        payload = valid_doc()
        payload["committed"] = False
        rejects(doc_bytes(payload), REST_UPDATED, "action_inconsistent")

    def test_state_changed_with_equal_shas_rejected(self) -> None:
        payload = valid_doc()
        payload["action"] = "state_changed"
        payload["product_sha"] = payload["source_sha"]
        rejects(doc_bytes(payload), update_rest("state_changed"), "action_inconsistent")


class TestBuildIdentityDocument:
    def _build(self, origin_main: str | None, action: str = "updated") -> bytes:
        return pi.build_identity_document(
            action=action,
            candidate_digest=_DIGEST,
            committed=action != "unchanged",
            product_sha=_SHA1 if action != "unchanged" else _OTHER_SHA1,
            repository=pi.REPOSITORY,
            source_sha=_OTHER_SHA1,
            workflow_run_attempt=RUN_ATTEMPT,
            workflow_run_id=RUN_ID,
            origin_main=origin_main,
            object_format="sha1",
        )

    def test_published_true_only_for_fresh_origin_equality(self) -> None:
        assert json.loads(self._build(_SHA1))["published"] is True
        assert json.loads(self._build(None))["published"] is False
        assert json.loads(self._build(_OTHER_SHA1))["published"] is False

    def test_build_roundtrips_validation(self) -> None:
        data = self._build(_SHA1)
        identity = pi.validate_identity_document(data, REST_UPDATED, object_format="sha1")
        assert identity.published is True

    def test_build_unchanged(self) -> None:
        identity = pi.validate_identity_document(
            self._build(None, action="unchanged"), REST_UNCHANGED
        )
        assert identity.action == "unchanged"
        assert identity.published is False

    def test_build_rejects_invalid_action(self) -> None:
        with pytest.raises(pi.ProductIdentityError):
            self._build(None, action="bogus")

    def test_build_rejects_wrong_repository(self) -> None:
        with pytest.raises(pi.ProductIdentityError):
            pi.build_identity_document(
                action="updated",
                candidate_digest=_DIGEST,
                committed=True,
                product_sha=_SHA1,
                repository="other/repo",
                source_sha=_OTHER_SHA1,
                workflow_run_attempt=RUN_ATTEMPT,
                workflow_run_id=RUN_ID,
                origin_main=None,
                object_format="sha1",
            )

    def test_build_rejects_non_oid_product_sha(self) -> None:
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.build_identity_document(
                action="updated",
                candidate_digest=_DIGEST,
                committed=True,
                product_sha="HEAD",
                repository=pi.REPOSITORY,
                source_sha=_OTHER_SHA1,
                workflow_run_attempt=RUN_ATTEMPT,
                workflow_run_id=RUN_ID,
                origin_main=None,
                object_format="sha1",
            )
        assert excinfo.value.code == "invalid_oid"

    def test_build_rejects_sha256_oid_on_sha1_repo(self) -> None:
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.build_identity_document(
                action="updated",
                candidate_digest=_DIGEST,
                committed=True,
                product_sha="a" * 64,
                repository=pi.REPOSITORY,
                source_sha="b" * 64,
                workflow_run_attempt=RUN_ATTEMPT,
                workflow_run_id=RUN_ID,
                origin_main=None,
                object_format="sha1",
            )
        assert excinfo.value.code == "invalid_oid"


# --- B. OID grammar and commit identity ----------------------------------------


class TestOidGrammar:
    def test_sha1_valid(self) -> None:
        assert pi.validate_oid_text("0" * 40, "sha1") == "0" * 40

    def test_sha256_valid(self) -> None:
        assert pi.validate_oid_text("0" * 64, "sha256") == "0" * 64

    @pytest.mark.parametrize(
        "oid",
        [
            "0" * 39,
            "0" * 41,
            "A" * 40,
            "0" * 39 + "F",
            " 0" * 20,
            "0" * 40 + "\n",
            "0" * 40 + " ",
            "main",
            "HEAD",
            "@",
            "HEAD~1",
            "HEAD^",
            "refs/heads/main",
            "",
            "0" * 62 + "gg",
            "0" * 40 + " --upload-pack=evil",
        ],
    )
    def test_sha1_invalid(self, oid: str) -> None:
        with pytest.raises(pi.ProductIdentityError):
            pi.validate_oid_text(oid, "sha1")

    def test_sha1_oid_rejected_in_sha256_repo(self) -> None:
        with pytest.raises(pi.ProductIdentityError):
            pi.validate_oid_text("0" * 40, "sha256")

    def test_sha256_oid_rejected_in_sha1_repo(self) -> None:
        with pytest.raises(pi.ProductIdentityError):
            pi.validate_oid_text("0" * 64, "sha1")

    def test_unsupported_format(self) -> None:
        with pytest.raises(pi.ProductIdentityError):
            pi.validate_oid_text("0" * 40, "sha3")

    def test_non_string_rejected(self) -> None:
        with pytest.raises(pi.ProductIdentityError):
            pi.validate_oid_text(40, "sha1")  # type: ignore[arg-type]


class TestRequireCommit:
    def test_object_format_detection(self, tmp_path: Path) -> None:
        repo, _ = make_base_repo(tmp_path)
        assert pi.git_object_format(repo) == "sha1"

    def test_object_format_detection_sha256(self, tmp_path: Path) -> None:
        repo, _ = make_base_repo(tmp_path, object_format="sha256")
        assert pi.git_object_format(repo) == "sha256"

    def test_commit_accepted(self, tmp_path: Path) -> None:
        repo, source = make_base_repo(tmp_path)
        assert pi.require_commit(repo, source) == source

    def test_tag_object_rejected(self, tmp_path: Path) -> None:
        repo, source = make_base_repo(tmp_path)
        _git(repo, "tag", "-a", "v9", "-m", "t", source)
        tag_oid = _git(repo, "rev-parse", "v9").stdout.strip()
        assert tag_oid != source
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.require_commit(repo, tag_oid)
        assert excinfo.value.code == "not_a_commit"

    def test_tree_rejected(self, tmp_path: Path) -> None:
        repo, source = make_base_repo(tmp_path)
        tree_oid = _git(repo, "rev-parse", f"{source}^{{tree}}").stdout.strip()
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.require_commit(repo, tree_oid)
        assert excinfo.value.code == "not_a_commit"

    def test_blob_rejected(self, tmp_path: Path) -> None:
        repo, source = make_base_repo(tmp_path)
        blob_oid = _git(repo, "rev-parse", f"{source}:catalog.json").stdout.strip()
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.require_commit(repo, blob_oid)
        assert excinfo.value.code == "not_a_commit"

    def test_unknown_oid_rejected(self, tmp_path: Path) -> None:
        repo, _ = make_base_repo(tmp_path)
        with pytest.raises(pi.ProductIdentityError):
            pi.require_commit(repo, "e" * 40)

    def test_sha256_repo_oid_length(self, tmp_path: Path) -> None:
        repo, source = make_base_repo(tmp_path, object_format="sha256")
        assert len(source) == 64
        assert pi.require_commit(repo, source, object_format="sha256") == source
        with pytest.raises(pi.ProductIdentityError):
            pi.require_commit(repo, source, object_format="sha1")


# --- C. action classification over raw git objects ------------------------------


class TestValidateAction:
    def test_unchanged_valid(self, tmp_path: Path) -> None:
        repo, source = make_base_repo(tmp_path)
        pi.validate_action(
            repo,
            action="unchanged",
            source_oid=source,
            product_oid=source,
            candidate_digest=pi.digest_commit_product(repo, source),
        )

    def test_unchanged_digest_mismatch(self, tmp_path: Path) -> None:
        repo, source = make_base_repo(tmp_path)
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.validate_action(
                repo,
                action="unchanged",
                source_oid=source,
                product_oid=source,
                candidate_digest=pi.candidate_digest(),
            )
        assert excinfo.value.code == "digest_mismatch"

    def test_unchanged_requires_equal_oids(self, tmp_path: Path) -> None:
        repo, source = make_base_repo(tmp_path)
        child = _commit(repo, "child", changes={"catalog.json": b'{"n":1}'})
        with pytest.raises(pi.ProductIdentityError):
            pi.validate_action(
                repo,
                action="unchanged",
                source_oid=source,
                product_oid=child,
                candidate_digest=pi.digest_commit_product(repo, child),
            )

    def test_state_changed_valid(self, tmp_path: Path) -> None:
        repo, source = make_base_repo(tmp_path)
        child = _commit(repo, "state", changes={"catalog.json": b'{"n":1}'})
        pi.validate_action(
            repo,
            action="state_changed",
            source_oid=source,
            product_oid=child,
            candidate_digest=pi.digest_commit_product(repo, child),
        )

    def test_state_changed_rejects_feed_change(self, tmp_path: Path) -> None:
        repo, source = make_base_repo(tmp_path)
        child = _commit(
            repo,
            "too much",
            changes={"catalog.json": b'{"n":1}', "feeds/rss.xml": b"<rss v2/>"},
        )
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.validate_action(
                repo,
                action="state_changed",
                source_oid=source,
                product_oid=child,
                candidate_digest=pi.digest_commit_product(repo, child),
            )
        assert excinfo.value.code == "action_paths_invalid"

    def test_updated_valid(self, tmp_path: Path) -> None:
        repo, source = make_base_repo(tmp_path)
        child = _commit(
            repo,
            "update",
            changes={"catalog.json": b'{"n":1}', "feeds/rss.xml": b"<rss v2/>"},
        )
        pi.validate_action(
            repo,
            action="updated",
            source_oid=source,
            product_oid=child,
            candidate_digest=pi.digest_commit_product(repo, child),
        )

    def test_updated_rejects_missing_catalog(self, tmp_path: Path) -> None:
        repo, source = make_base_repo(tmp_path)
        child = _commit(repo, "feed only", changes={"feeds/rss.xml": b"<rss v2/>"})
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.validate_action(
                repo,
                action="updated",
                source_oid=source,
                product_oid=child,
                candidate_digest=pi.digest_commit_product(repo, child),
            )
        assert excinfo.value.code == "action_paths_invalid"

    def test_updated_rejects_non_product_path(self, tmp_path: Path) -> None:
        repo, source = make_base_repo(tmp_path)
        child = _commit(
            repo,
            "stray",
            changes={"catalog.json": b'{"n":1}', "README.md": b"hi"},
        )
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.validate_action(
                repo,
                action="updated",
                source_oid=source,
                product_oid=child,
                candidate_digest=pi.digest_commit_product(repo, child),
            )
        assert excinfo.value.code == "non_product_path"

    def test_rejects_deleted_product_leaf(self, tmp_path: Path) -> None:
        repo, source = make_base_repo(tmp_path)
        child = _commit(
            repo,
            "delete",
            changes={"catalog.json": b'{"n":1}', "feeds/rss.xml": None},
        )
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.validate_action(
                repo,
                action="updated",
                source_oid=source,
                product_oid=child,
                candidate_digest=pi.candidate_digest(),
            )
        assert excinfo.value.code == "product_path_deleted"

    def test_rejects_added_feeds_extra(self, tmp_path: Path) -> None:
        repo, source = make_base_repo(tmp_path)
        child = _commit(
            repo,
            "extra",
            changes={"catalog.json": b'{"n":1}', "feeds/extra.xml": b"<extra/>"},
        )
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.validate_action(
                repo,
                action="updated",
                source_oid=source,
                product_oid=child,
                candidate_digest=pi.candidate_digest(),
            )
        assert excinfo.value.code == "non_product_path"

    def test_rejects_case_variant_feed(self, tmp_path: Path) -> None:
        repo, source = make_base_repo(tmp_path)
        child = _commit(
            repo,
            "case",
            changes={"catalog.json": b'{"n":1}', "feeds/RSS.xml": b"<x/>"},
        )
        with pytest.raises(pi.ProductIdentityError):
            pi.validate_action(
                repo,
                action="updated",
                source_oid=source,
                product_oid=child,
                candidate_digest=pi.candidate_digest(),
            )

    def test_rejects_nested_feed_directory(self, tmp_path: Path) -> None:
        repo, source = make_base_repo(tmp_path)
        child = _commit(
            repo,
            "nested",
            changes={"catalog.json": b'{"n":1}', "feeds/sub/item.xml": b"<x/>"},
        )
        with pytest.raises(pi.ProductIdentityError):
            pi.validate_action(
                repo,
                action="updated",
                source_oid=source,
                product_oid=child,
                candidate_digest=pi.candidate_digest(),
            )

    def test_rejects_executable_mode(self, tmp_path: Path) -> None:
        repo, source = make_base_repo(tmp_path)
        _write(repo, "catalog.json", b'{"n":1}')
        _git(repo, "add", "-A")
        _git(repo, "update-index", "--chmod=+x", "catalog.json")
        _git(repo, "commit", "-q", "-m", "exec")
        child = _git(repo, "rev-parse", "HEAD").stdout.strip()
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.validate_action(
                repo,
                action="updated",
                source_oid=source,
                product_oid=child,
                candidate_digest=pi.candidate_digest(),
            )
        assert excinfo.value.code in {"product_mode_violation", "product_tree_invalid"}

    def test_rejects_mode_only_change(self, tmp_path: Path) -> None:
        repo, source = make_base_repo(tmp_path)
        _git(repo, "update-index", "--chmod=+x", "catalog.json")
        _git(repo, "commit", "-q", "-m", "mode only")
        child = _git(repo, "rev-parse", "HEAD").stdout.strip()
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.validate_action(
                repo,
                action="updated",
                source_oid=source,
                product_oid=child,
                candidate_digest=pi.digest_commit_product(repo, child),
            )
        assert excinfo.value.code in {"product_mode_violation", "product_tree_invalid"}

    def test_rejects_symlink_leaf(self, tmp_path: Path) -> None:
        repo, source = make_base_repo(tmp_path)
        (repo / "feeds" / "rss.xml").unlink()
        os.symlink("../catalog.json", repo / "feeds" / "rss.xml")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "link")
        child = _git(repo, "rev-parse", "HEAD").stdout.strip()
        with pytest.raises(pi.ProductIdentityError):
            pi.validate_action(
                repo,
                action="updated",
                source_oid=source,
                product_oid=child,
                candidate_digest=pi.candidate_digest(),
            )

    def test_rejects_merge_commit(self, tmp_path: Path) -> None:
        repo, source = make_base_repo(tmp_path)
        first = _commit(repo, "one", changes={"catalog.json": b'{"n":1}'})
        _git(repo, "checkout", "-q", "-b", "side", source)
        _commit(repo, "two", changes={"feeds/rss.xml": b"<rss v2/>"})
        _git(repo, "checkout", "-q", "main")
        _git(repo, "merge", "-q", "--no-ff", "-m", "merge", "side")
        merged = _git(repo, "rev-parse", "HEAD").stdout.strip()
        assert first != merged
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.validate_action(
                repo,
                action="updated",
                source_oid=source,
                product_oid=merged,
                candidate_digest=pi.digest_commit_product(repo, merged),
            )
        assert excinfo.value.code == "not_a_direct_child"

    def test_rejects_grandchild(self, tmp_path: Path) -> None:
        repo, source = make_base_repo(tmp_path)
        _commit(repo, "one", changes={"catalog.json": b'{"n":1}'})
        grandchild = _commit(repo, "two", changes={"feeds/rss.xml": b"<rss v2/>"})
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.validate_action(
                repo,
                action="updated",
                source_oid=source,
                product_oid=grandchild,
                candidate_digest=pi.digest_commit_product(repo, grandchild),
            )
        assert excinfo.value.code == "not_a_direct_child"

    def test_rejects_root_commit_without_parent(self, tmp_path: Path) -> None:
        repo, source = make_base_repo(tmp_path)
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.validate_action(
                repo,
                action="updated",
                source_oid=source,
                product_oid=source,
                candidate_digest=pi.digest_commit_product(repo, source),
            )
        assert excinfo.value.code == "not_a_direct_child"

    def test_digest_uses_git_blobs_not_workspace(self, tmp_path: Path) -> None:
        repo, source = make_base_repo(tmp_path)
        (repo / "feeds" / "rss.xml").write_bytes(b"tampered")
        assert pi.digest_commit_product(repo, source) == pi.candidate_digest(BASE_FILES)

    def test_invalid_action_name(self, tmp_path: Path) -> None:
        repo, source = make_base_repo(tmp_path)
        with pytest.raises(pi.ProductIdentityError):
            pi.validate_action(
                repo,
                action="bogus",
                source_oid=source,
                product_oid=source,
                candidate_digest=_DIGEST,
            )

    def test_sha256_repository_actions(self, tmp_path: Path) -> None:
        repo, source = make_base_repo(tmp_path, object_format="sha256")
        child = _commit(
            repo, "update", changes={"catalog.json": b'{"n":1}', "feeds/rss.xml": b"<rss v2/>"}
        )
        pi.validate_action(
            repo,
            action="updated",
            source_oid=source,
            product_oid=child,
            candidate_digest=pi.digest_commit_product(repo, child),
        )


class TestProductTree:
    def test_tree_entries_complete(self, tmp_path: Path) -> None:
        repo, source = make_base_repo(tmp_path)
        entries = pi.product_tree_entries(repo, source)
        assert set(entries) == set(pi.PRODUCT_SUBJECTS)
        assert all(e.mode == "100644" and e.kind == "blob" for e in entries.values())

    def test_read_product_blobs(self, tmp_path: Path) -> None:
        repo, source = make_base_repo(tmp_path)
        assert pi.read_product_blobs(repo, source) == BASE_FILES

    def test_missing_feed_rejected(self, tmp_path: Path) -> None:
        repo, _source = make_base_repo(tmp_path)
        child = _commit(repo, "rm", changes={"feeds/rss.xml": None, "catalog.json": b'{"n":1}'})
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.product_tree_entries(repo, child)
        assert excinfo.value.code == "product_tree_invalid"

    def test_gitlink_rejected(self, tmp_path: Path) -> None:
        repo, source = make_base_repo(tmp_path)
        _git(
            repo,
            "update-index",
            "--add",
            "--cacheinfo",
            "160000",
            source,
            "feeds/evil",
        )
        _git(repo, "commit", "-q", "-m", "gitlink")
        child = _git(repo, "rev-parse", "HEAD").stdout.strip()
        with pytest.raises(pi.ProductIdentityError):
            pi.product_tree_entries(repo, child)


# --- D. candidate digest --------------------------------------------------------


class TestCandidateDigest:
    def test_vector_seven_empty(self) -> None:
        assert pi.candidate_digest() == (
            "sha256:bfefc985ecff5f3371caad965550e87030516210cd75b50651e0d2f5f5dfdffb"
        )

    def test_vector_two_subjects(self) -> None:
        assert (
            pi.candidate_digest({"catalog.json": b"{}", "feeds/rss.xml": b"<rss/>"})
            == "sha256:a5f4d606317d3802a000f24479234f79985fe8fc827efb205bfa397f0d9bcff4"
        )

    def test_vector_seven_x(self) -> None:
        assert pi.candidate_digest({s: b"x" for s in pi.PRODUCT_SUBJECTS}) == (
            "sha256:89717579026cea8365c607b7a8c55ed76d811441e33b18e49acca3b3c272217a"
        )

    def _manual(
        self,
        subjects: list[str],
        mapping: dict[str, bytes],
        *,
        nul: bool = True,
        endian: Literal["little", "big"] = "little",
        char_length: bool = False,
    ) -> str:
        hasher = hashlib.sha256()
        for rel in subjects:
            data = mapping.get(rel, b"")
            length = len(data.decode("utf-8", "replace")) if char_length else len(data)
            hasher.update(rel.encode("utf-8"))
            if nul:
                hasher.update(b"\x00")
            hasher.update(length.to_bytes(8, endian))
            hasher.update(data)
        return "sha256:" + hasher.hexdigest()

    def test_order_sensitivity(self) -> None:
        mapping = {s: b"x" for s in pi.PRODUCT_SUBJECTS}
        reversed_digest = self._manual(list(reversed(pi.PRODUCT_SUBJECTS)), mapping)
        assert reversed_digest != pi.candidate_digest(mapping)

    def test_little_endian_required(self) -> None:
        mapping = {s: b"payload" for s in pi.PRODUCT_SUBJECTS}
        big_endian = self._manual(list(pi.PRODUCT_SUBJECTS), mapping, endian="big")
        assert big_endian != pi.candidate_digest(mapping)

    def test_nul_separator_required(self) -> None:
        mapping = {"catalog.json": b"abc"}
        without_nul = self._manual(list(pi.PRODUCT_SUBJECTS), mapping, nul=False)
        assert without_nul != pi.candidate_digest(mapping)

    def test_missing_nul_is_ambiguous(self) -> None:
        # ("ab", b"c") vs ("a", b"bc"): path/data boundary ambiguity proves the
        # NUL separator's necessity.
        def plain(path: str, data: bytes) -> str:
            return "sha256:" + hashlib.sha256(path.encode() + data).hexdigest()

        assert plain("ab", b"c") == plain("a", b"bc")
        assert pi.frame_subject("ab", b"c") != pi.frame_subject("a", b"bc")

    def test_byte_length_not_char_length(self) -> None:
        data = "héllo".encode()  # 5 chars, 6 bytes
        frame = pi.frame_subject("feeds/rss.xml", data)
        nul = frame.index(b"\x00", len(b"feeds/rss.xml"))
        assert int.from_bytes(frame[nul + 1 : nul + 9], "little") == 6
        mapping = {s: data for s in pi.PRODUCT_SUBJECTS}
        char_based = self._manual(list(pi.PRODUCT_SUBJECTS), mapping, char_length=True)
        assert char_based != pi.candidate_digest(mapping)

    def test_large_subject_over_64k(self) -> None:
        big = b"z" * 70_000
        mapping = {s: (big if s == "catalog.json" else b"") for s in pi.PRODUCT_SUBJECTS}
        expected = self._manual(list(pi.PRODUCT_SUBJECTS), mapping)
        assert pi.candidate_digest(mapping) == expected
        mapping["catalog.json"] = b"z" * 70_001
        assert pi.candidate_digest(mapping) != expected

    def test_frame_subject_shape(self) -> None:
        assert pi.frame_subject("a", b"") == b"a\x00" + (0).to_bytes(8, "little")
        assert pi.frame_subject("a", b"bc") == b"a\x00" + (2).to_bytes(8, "little") + b"bc"


# --- E. publication reconciliation ---------------------------------------------


class TestPublication:
    def test_published_regardless_of_push_outcome(self) -> None:
        for outcome in ("command_ok", "command_error", "command_timeout"):
            assert pi.classify_publication(outcome, _SHA1, _OTHER_SHA1, _SHA1) == "published"

    def test_not_published_retryable_when_origin_is_source(self) -> None:
        assert (
            pi.classify_publication("command_ok", _OTHER_SHA1, _OTHER_SHA1, _SHA1)
            == "not_published_retryable"
        )

    def test_retry_bound_is_one(self) -> None:
        assert pi.MAX_PUSH_RETRIES == 1

    def test_not_adopted_for_any_other_oid(self) -> None:
        assert pi.classify_publication("command_ok", "e" * 40, _OTHER_SHA1, _SHA1) == "not_adopted"

    def test_not_adopted_even_with_identical_tree(self, tmp_path: Path) -> None:
        repo, source = make_base_repo(tmp_path)
        child = _commit(
            repo,
            "update",
            changes={"catalog.json": b'{"n":1}', "feeds/rss.xml": b"<rss v2/>"},
        )
        twin = _commit(repo, "twin of child")  # same tree, different commit
        assert twin != child
        assert pi.classify_publication("command_ok", twin, source, child) == "not_adopted"

    def test_unresolved_on_fetch_failure(self) -> None:
        assert pi.classify_publication("command_ok", None, _OTHER_SHA1, _SHA1) == "unresolved"

    def test_invalid_push_outcome_rejected(self) -> None:
        with pytest.raises(pi.ProductIdentityError):
            pi.classify_publication("exit_0", _SHA1, _OTHER_SHA1, _SHA1)

    def test_push_argv_exact(self) -> None:
        assert pi.build_push_argv(_SHA1) == [
            "git",
            "push",
            "--no-follow-tags",
            "origin",
            f"{_SHA1}:refs/heads/main",
        ]

    @pytest.mark.parametrize(
        "argv",
        [
            ["git", "push", "--force", "origin", "x:refs/heads/main"],
            ["git", "push", "-f", "origin", "x:refs/heads/main"],
            ["git", "push", "--force-with-lease", "origin", "x:refs/heads/main"],
            ["git", "push", "origin", "+x:refs/heads/main"],
        ],
    )
    def test_force_push_forbidden(self, argv: list[str]) -> None:
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.validate_push_argv(argv)
        assert excinfo.value.code == "force_push_forbidden"

    def test_validate_push_argv_accepts_exact_form(self) -> None:
        pi.validate_push_argv(pi.build_push_argv(_SHA1))


class TestPreexistingChild:
    def _setup(self, tmp_path: Path) -> tuple[Path, Path, str, str]:
        repo, source = make_base_repo(tmp_path)
        child = _commit(
            repo,
            "Update feeds",
            f"Update-Run-Id: {RUN_ID}\nUpdate-Run-Attempt: {RUN_ATTEMPT}",
            changes={"catalog.json": b'{"n":1}', "feeds/rss.xml": b"<rss v2/>"},
        )
        _, consumer = make_origin(tmp_path, repo, {"main": child, "source": source})
        pi.fetch_exact_commit(consumer, source)
        pi.fetch_exact_commit(consumer, child)
        return repo, consumer, source, child

    def test_fresh_rerun_adopts_valid_child(self, tmp_path: Path) -> None:
        repo, consumer, source, child = self._setup(tmp_path)
        pi.validate_preexisting_child(
            consumer,
            child_oid=child,
            source_oid=source,
            action="updated",
            candidate_digest=pi.digest_commit_product(repo, child),
            rest=update_rest("updated"),
            origin_main=child,
        )

    def test_rejects_origin_mismatch(self, tmp_path: Path) -> None:
        repo, consumer, source, child = self._setup(tmp_path)
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.validate_preexisting_child(
                consumer,
                child_oid=child,
                source_oid=source,
                action="updated",
                candidate_digest=pi.digest_commit_product(repo, child),
                rest=update_rest("updated"),
                origin_main=source,
            )
        assert excinfo.value.code == "origin_mismatch"

    def test_rejects_trailer_run_id_mismatch(self, tmp_path: Path) -> None:
        repo, consumer, source, child = self._setup(tmp_path)
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.validate_preexisting_child(
                consumer,
                child_oid=child,
                source_oid=source,
                action="updated",
                candidate_digest=pi.digest_commit_product(repo, child),
                rest=update_rest("updated", run_id=RUN_ID + 1),
                origin_main=child,
            )
        assert excinfo.value.code == "trailer_mismatch"

    def test_rejects_trailer_attempt_mismatch(self, tmp_path: Path) -> None:
        repo, consumer, source, child = self._setup(tmp_path)
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.validate_preexisting_child(
                consumer,
                child_oid=child,
                source_oid=source,
                action="updated",
                candidate_digest=pi.digest_commit_product(repo, child),
                rest=update_rest("updated", run_attempt=2),
                origin_main=child,
            )
        assert excinfo.value.code == "trailer_mismatch"

    def test_rejects_missing_trailers(self, tmp_path: Path) -> None:
        repo, source = make_base_repo(tmp_path)
        child = _commit(
            repo,
            "Update feeds",
            changes={"catalog.json": b'{"n":1}', "feeds/rss.xml": b"<rss v2/>"},
        )
        _, consumer = make_origin(tmp_path, repo, {"main": child, "source": source})
        pi.fetch_exact_commit(consumer, source)
        pi.fetch_exact_commit(consumer, child)
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.validate_preexisting_child(
                consumer,
                child_oid=child,
                source_oid=source,
                action="updated",
                candidate_digest=pi.digest_commit_product(repo, child),
                rest=update_rest("updated"),
                origin_main=child,
            )
        assert excinfo.value.code == "trailer_missing"

    def test_rejects_invalid_action_paths(self, tmp_path: Path) -> None:
        repo, source = make_base_repo(tmp_path)
        child = _commit(
            repo,
            "Update feeds",
            f"Update-Run-Id: {RUN_ID}\nUpdate-Run-Attempt: {RUN_ATTEMPT}",
            changes={"README.md": b"hi"},
        )
        _, consumer = make_origin(tmp_path, repo, {"main": child, "source": source})
        pi.fetch_exact_commit(consumer, source)
        pi.fetch_exact_commit(consumer, child)
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.validate_preexisting_child(
                consumer,
                child_oid=child,
                source_oid=source,
                action="updated",
                candidate_digest=pi.candidate_digest(),
                rest=update_rest("updated"),
                origin_main=child,
            )
        assert excinfo.value.code == "non_product_path"

    def test_rejects_digest_mismatch(self, tmp_path: Path) -> None:
        _repo, consumer, source, child = self._setup(tmp_path)
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.validate_preexisting_child(
                consumer,
                child_oid=child,
                source_oid=source,
                action="updated",
                candidate_digest=pi.candidate_digest(),
                rest=update_rest("updated"),
                origin_main=child,
            )
        assert excinfo.value.code == "digest_mismatch"

    def test_rejects_non_child_relation(self, tmp_path: Path) -> None:
        repo, consumer, source, _child = self._setup(tmp_path)
        grandchild = _commit(repo, "later")
        _git(
            repo, "push", "-q", str(tmp_path / "origin.git"), f"{grandchild}:refs/heads/grandchild"
        )
        pi.fetch_exact_commit(consumer, grandchild)
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.validate_preexisting_child(
                consumer,
                child_oid=grandchild,
                source_oid=source,
                action="updated",
                candidate_digest=pi.digest_commit_product(repo, grandchild),
                rest=update_rest("updated"),
                origin_main=grandchild,
            )
        assert excinfo.value.code == "not_a_direct_child"

    def test_same_attempt_substitution_forbidden(self, tmp_path: Path) -> None:
        repo, consumer, source, child = self._setup(tmp_path)
        other = _commit(
            repo,
            "Update feeds",
            f"Update-Run-Id: {RUN_ID}\nUpdate-Run-Attempt: {RUN_ATTEMPT}",
            changes={"catalog.json": b'{"n":2}', "feeds/atom.xml": b"<atom v2/>"},
        )
        assert other != child
        _git(repo, "push", "-q", str(tmp_path / "origin.git"), f"{other}:refs/heads/other")
        pi.fetch_exact_commit(consumer, other)
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.validate_preexisting_child(
                consumer,
                child_oid=other,
                source_oid=source,
                action="updated",
                candidate_digest=pi.digest_commit_product(repo, other),
                rest=update_rest("updated"),
                origin_main=other,
                attempted_commit_oid=child,
            )
        assert excinfo.value.code == "same_attempt_substitution"

    def test_own_proposed_commit_still_valid(self, tmp_path: Path) -> None:
        repo, consumer, source, child = self._setup(tmp_path)
        pi.validate_preexisting_child(
            consumer,
            child_oid=child,
            source_oid=source,
            action="updated",
            candidate_digest=pi.digest_commit_product(repo, child),
            rest=update_rest("updated"),
            origin_main=child,
            attempted_commit_oid=child,
        )


class TestCommitTrailers:
    def test_parses_trailers(self, tmp_path: Path) -> None:
        repo, _source = make_base_repo(tmp_path)
        child = _commit(
            repo,
            "Update feeds",
            f"Update-Run-Id: {RUN_ID}\nUpdate-Run-Attempt: {RUN_ATTEMPT}",
        )
        trailers = pi.commit_trailers(repo, child)
        assert trailers[pi.UPDATE_RUN_ID_TRAILER] == [str(RUN_ID)]
        assert trailers[pi.UPDATE_RUN_ATTEMPT_TRAILER] == [str(RUN_ATTEMPT)]

    def test_missing_trailers(self, tmp_path: Path) -> None:
        repo, _ = make_base_repo(tmp_path)
        assert pi.UPDATE_RUN_ID_TRAILER not in pi.commit_trailers(repo, _head(repo))

    def test_duplicate_trailer_lines(self, tmp_path: Path) -> None:
        repo, _ = make_base_repo(tmp_path)
        child = _commit(
            repo,
            "Update feeds",
            f"Update-Run-Id: {RUN_ID}\nUpdate-Run-Id: {RUN_ID + 1}",
        )
        assert len(pi.commit_trailers(repo, child)[pi.UPDATE_RUN_ID_TRAILER]) == 2


def _head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


# --- F. artifact consumption contract ------------------------------------------


class TestArtifacts:
    def test_expected_artifact_name(self) -> None:
        assert pi.expected_artifact_name(RUN_ID, RUN_ATTEMPT) == "product-identity-123456789-1"

    def test_expected_artifact_name_rejects_bad_ids(self) -> None:
        with pytest.raises(pi.ProductIdentityError):
            pi.expected_artifact_name(0, 1)
        with pytest.raises(pi.ProductIdentityError):
            pi.expected_artifact_name(1, True)  # type: ignore[arg-type]

    def _record(self, **overrides: Any) -> pi.ArtifactRecord:
        fields: dict[str, Any] = {
            "artifact_id": 42,
            "name": pi.expected_artifact_name(RUN_ID, RUN_ATTEMPT),
            "expired": False,
            "archive_digest": "sha256:" + "0" * 64,
        }
        fields.update(overrides)
        return pi.ArtifactRecord(**fields)

    def test_selects_unique_active_artifact(self) -> None:
        record = pi.select_identity_artifact(
            [self._record(), self._record(name="other-1", artifact_id=43)],
            run_id=RUN_ID,
            run_attempt=RUN_ATTEMPT,
        )
        assert record.artifact_id == 42

    def test_rejects_zero_matches(self) -> None:
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.select_identity_artifact([], run_id=RUN_ID, run_attempt=RUN_ATTEMPT)
        assert excinfo.value.code == "artifact_selection"

    def test_rejects_two_active_matches(self) -> None:
        with pytest.raises(pi.ProductIdentityError):
            pi.select_identity_artifact(
                [self._record(), self._record(artifact_id=43)],
                run_id=RUN_ID,
                run_attempt=RUN_ATTEMPT,
            )

    def test_rejects_only_expired_match(self) -> None:
        with pytest.raises(pi.ProductIdentityError):
            pi.select_identity_artifact(
                [self._record(expired=True)],
                run_id=RUN_ID,
                run_attempt=RUN_ATTEMPT,
            )

    def test_accepts_single_active_despite_expired_duplicate(self) -> None:
        record = pi.select_identity_artifact(
            [self._record(), self._record(expired=True, artifact_id=43)],
            run_id=RUN_ID,
            run_attempt=RUN_ATTEMPT,
        )
        assert record.artifact_id == 42

    def test_run_attempt_scoped_name(self) -> None:
        with pytest.raises(pi.ProductIdentityError):
            pi.select_identity_artifact(
                [self._record(name="product-identity-123456789-2")],
                run_id=RUN_ID,
                run_attempt=RUN_ATTEMPT,
            )

    @pytest.mark.parametrize(
        "digest",
        ["A" * 64, "0" * 63, "sha256:", "sha512:" + "0" * 128, "", None, 7],
    )
    def test_archive_digest_is_archive_field_not_payload_hash(self, digest: Any) -> None:
        with pytest.raises(pi.ProductIdentityError):
            pi.validate_archive_digest(digest)

    def test_archive_digest_valid(self) -> None:
        assert pi.validate_archive_digest("sha256:" + "a" * 64)

    def test_artifact_id_positive_integer(self) -> None:
        assert pi.validate_artifact_id(1) == 1
        for bad in (0, -1, True, "42", 1.0, None):
            with pytest.raises(pi.ProductIdentityError):
                pi.validate_artifact_id(bad)

    def test_download_directory_must_be_fresh_and_empty(self, tmp_path: Path) -> None:
        fresh = tmp_path / "fresh"
        fresh.mkdir()
        pi.validate_download_directory(fresh)

    def test_download_directory_rejects_missing(self, tmp_path: Path) -> None:
        with pytest.raises(pi.ProductIdentityError):
            pi.validate_download_directory(tmp_path / "absent")

    def test_download_directory_rejects_nonempty(self, tmp_path: Path) -> None:
        target = tmp_path / "used"
        target.mkdir()
        (target / "f").write_bytes(b"x")
        with pytest.raises(pi.ProductIdentityError):
            pi.validate_download_directory(target)

    def test_download_directory_rejects_symlink(self, tmp_path: Path) -> None:
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        os.symlink(real, link)
        with pytest.raises(pi.ProductIdentityError):
            pi.validate_download_directory(link)

    def test_download_directory_rejects_file(self, tmp_path: Path) -> None:
        target = tmp_path / "file"
        target.write_bytes(b"x")
        with pytest.raises(pi.ProductIdentityError):
            pi.validate_download_directory(target)

    def test_require_published(self) -> None:
        data = pi.build_identity_document(
            action="updated",
            candidate_digest=_DIGEST,
            committed=True,
            product_sha=_SHA1,
            repository=pi.REPOSITORY,
            source_sha=_OTHER_SHA1,
            workflow_run_attempt=RUN_ATTEMPT,
            workflow_run_id=RUN_ID,
            origin_main=_SHA1,
            object_format="sha1",
        )
        identity = pi.validate_identity_document(data, REST_UPDATED, object_format="sha1")
        assert identity.published is True
        pi.require_published(identity)

    def test_require_published_rejects_false(self) -> None:
        payload = valid_doc()
        payload["published"] = False
        identity = pi.validate_identity_document(doc_bytes(payload), REST_UPDATED)
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.require_published(identity)
        assert excinfo.value.code == "not_published"


# --- G. exact-OID object acquisition --------------------------------------------


class TestFetchExactCommit:
    def _origin_pair(self, tmp_path: Path) -> tuple[Path, Path, str, str, str]:
        repo, source = make_base_repo(tmp_path, name="prod")
        child = _commit(
            repo,
            "Update feeds",
            changes={"catalog.json": b'{"n":1}', "feeds/rss.xml": b"<rss v2/>"},
        )
        _, consumer = make_origin(tmp_path, repo, {"main": child, "source": source})
        return repo, consumer, source, child, "unused"

    def test_fetch_argv_exact(self) -> None:
        assert pi.build_fetch_argv(_SHA1) == ["git", "fetch", "--no-tags", "origin", _SHA1]

    def test_fetch_bring_exact_commit(self, tmp_path: Path) -> None:
        _, consumer, source, child, _ = self._origin_pair(tmp_path)
        assert pi.fetch_exact_commit(consumer, child) == child
        assert pi.fetch_exact_commit(consumer, source) == source
        assert pi.require_commit(consumer, child) == child
        assert pi.require_commit(consumer, source) == source

    def test_fetch_unknown_oid_fails(self, tmp_path: Path) -> None:
        _, consumer, _, _, _ = self._origin_pair(tmp_path)
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.fetch_exact_commit(consumer, "e" * 40)
        assert excinfo.value.code == "fetch_failed"

    def test_fetch_rejects_grammar_before_subprocess(self, tmp_path: Path) -> None:
        _, consumer, _, _, _ = self._origin_pair(tmp_path)
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.fetch_exact_commit(consumer, "HEAD")
        assert excinfo.value.code == "invalid_oid"

    def test_fetch_head_mismatch_detected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, consumer, source, child, _ = self._origin_pair(tmp_path)
        fetch_head = consumer / ".git" / "FETCH_HEAD"
        fetch_head.write_text(f"{source}\t\t'x' of origin\n", encoding="utf-8")

        def fake_run_git(repo: Path, args: Any, **kwargs: Any) -> Any:
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(pi, "run_git", fake_run_git)
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.fetch_exact_commit(consumer, child, object_format="sha1")
        assert excinfo.value.code == "fetch_unverified"

    def test_tree_present_required(self, tmp_path: Path) -> None:
        _, consumer, _, child, _ = self._origin_pair(tmp_path)
        pi.fetch_exact_commit(consumer, child)
        assert len(pi.ensure_tree_present(consumer, child)) == 40

    def test_fetch_runs_once_when_source_equals_product(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo, source = make_base_repo(tmp_path, name="prod")
        _, consumer = make_origin(tmp_path, repo, {"main": source})
        calls: list[list[str]] = []
        real = pi.run_git

        def counting(repo: Path, args: Any, **kwargs: Any) -> Any:
            calls.append(list(args))
            return real(repo, args, **kwargs)

        monkeypatch.setattr(pi, "run_git", counting)
        pi.fetch_exact_commit(consumer, source)
        fetches = [c for c in calls if c and c[0] == "fetch"]
        assert fetches == [["fetch", "--no-tags", "origin", source]]

    def test_two_fetches_when_source_and_product_distinct(self, tmp_path: Path) -> None:
        repo, source = make_base_repo(tmp_path, name="prod")
        child = _commit(
            repo,
            "Update feeds",
            changes={"catalog.json": b'{"n":1}', "feeds/rss.xml": b"<rss v2/>"},
        )
        _, consumer = make_origin(tmp_path, repo, {"main": child, "source": source})
        pi.fetch_exact_commit(consumer, source)
        pi.fetch_exact_commit(consumer, child)
        assert pi.require_commit(consumer, source) == source
        assert pi.require_commit(consumer, child) == child


class TestSufficientHistory:
    def _shallow_consumer(self, tmp_path: Path) -> tuple[Path, str, str]:
        repo, source = make_base_repo(tmp_path, name="prod")
        child = _commit(
            repo,
            "Update feeds",
            changes={"catalog.json": b'{"n":1}', "feeds/rss.xml": b"<rss v2/>"},
        )
        bare = tmp_path / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True, capture_output=True)
        _git(
            repo, "push", "-q", str(bare), f"{child}:refs/heads/main", f"{source}:refs/heads/source"
        )
        shallow = tmp_path / "shallow"
        subprocess.run(
            [
                "git",
                "-c",
                "protocol.file.allow=always",
                "clone",
                "-q",
                "--depth",
                "1",
                "--no-local",
                "--no-hardlinks",
                f"file://{bare}",
                str(shallow),
            ],
            check=True,
            capture_output=True,
        )
        # Do not fetch the parent OID: that can unshallow or drop `.git/shallow`
        # on some git versions and is not required to prove fail-closed ancestry.
        return shallow, source, child

    def test_shallow_clone_detects_boundary(self, tmp_path: Path) -> None:
        shallow, _source, child = self._shallow_consumer(tmp_path)
        is_shallow = _git(shallow, "rev-parse", "--is-shallow-repository").stdout.strip() == "true"
        git_path = Path(_git(shallow, "rev-parse", "--git-path", "shallow").stdout.strip())
        shallow_file = git_path if git_path.is_absolute() else shallow / git_path
        assert is_shallow or shallow_file.is_file()
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.require_sufficient_history(shallow, child)
        assert excinfo.value.code == "insufficient_history"
        assert pi.INSUFFICIENT_HISTORY_MESSAGE in str(excinfo.value)

    def test_shallow_clone_parents_are_grafted(self, tmp_path: Path) -> None:
        shallow, _, child = self._shallow_consumer(tmp_path)
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.parent_oids(shallow, child)
        assert excinfo.value.code == "insufficient_history"
        assert pi.INSUFFICIENT_HISTORY_MESSAGE in str(excinfo.value)

    def test_validate_action_fails_closed_on_shallow(self, tmp_path: Path) -> None:
        shallow, source, child = self._shallow_consumer(tmp_path)
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.validate_action(
                shallow,
                action="updated",
                source_oid=source,
                product_oid=child,
                candidate_digest=pi.candidate_digest(),
            )
        assert excinfo.value.code == "insufficient_history"
        assert pi.INSUFFICIENT_HISTORY_MESSAGE in str(excinfo.value)

    def test_non_shallow_history_passes(self, tmp_path: Path) -> None:
        repo, source = make_base_repo(tmp_path)
        child = _commit(
            repo,
            "Update feeds",
            changes={"catalog.json": b'{"n":1}', "feeds/rss.xml": b"<rss v2/>"},
        )
        pi.require_sufficient_history(repo, source, child)
        assert pi.parent_oids(repo, child) == [source]


# --- H. physical candidate-tree validation --------------------------------------


class TestCandidateTree:
    def test_valid_tree_returns_bytes(self, tmp_path: Path) -> None:
        root = tree_root(tmp_path)
        assert pi.validate_candidate_tree(root) == BASE_FILES

    def test_digest_from_validated_tree(self, tmp_path: Path) -> None:
        assert pi.candidate_digest_from_tree(tree_root(tmp_path)) == pi.candidate_digest(BASE_FILES)

    def test_extra_root_file_rejected(self, tmp_path: Path) -> None:
        root = tree_root(tmp_path)
        (root / "stray.txt").write_bytes(b"x")
        with pytest.raises(pi.ProductIdentityError):
            pi.validate_candidate_tree(root)

    def test_missing_catalog_rejected(self, tmp_path: Path) -> None:
        root = tree_root(tmp_path)
        (root / "catalog.json").unlink()
        with pytest.raises(pi.ProductIdentityError):
            pi.validate_candidate_tree(root)

    def test_missing_feeds_dir_rejected(self, tmp_path: Path) -> None:
        root = tree_root(tmp_path)
        shutil.rmtree(root / "feeds")
        with pytest.raises(pi.ProductIdentityError):
            pi.validate_candidate_tree(root)

    def test_root_symlink_rejected(self, tmp_path: Path) -> None:
        real = tree_root(tmp_path)
        link = tmp_path / "linkroot"
        os.symlink(real, link)
        with pytest.raises(pi.ProductIdentityError):
            pi.validate_candidate_tree(link)

    def test_root_missing_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(pi.ProductIdentityError):
            pi.validate_candidate_tree(tmp_path / "absent")

    def test_feeds_symlink_dir_rejected(self, tmp_path: Path) -> None:
        root = tree_root(tmp_path)
        real_feeds = tmp_path / "real_feeds"
        real_feeds.mkdir()
        shutil.rmtree(root / "feeds")
        os.symlink(real_feeds, root / "feeds")
        with pytest.raises(pi.ProductIdentityError):
            pi.validate_candidate_tree(root)

    def test_feeds_regular_file_rejected(self, tmp_path: Path) -> None:
        root = tree_root(tmp_path)
        shutil.rmtree(root / "feeds")
        (root / "feeds").write_bytes(b"x")
        with pytest.raises(pi.ProductIdentityError):
            pi.validate_candidate_tree(root)

    def test_catalog_symlink_rejected(self, tmp_path: Path) -> None:
        root = tree_root(tmp_path)
        (root / "catalog.json").unlink()
        os.symlink(tmp_path / "outside", root / "catalog.json")
        with pytest.raises(pi.ProductIdentityError):
            pi.validate_candidate_tree(root)

    def test_catalog_directory_rejected(self, tmp_path: Path) -> None:
        root = tree_root(tmp_path)
        (root / "catalog.json").unlink()
        (root / "catalog.json").mkdir()
        with pytest.raises(pi.ProductIdentityError):
            pi.validate_candidate_tree(root)

    def test_broken_symlink_classified(self, tmp_path: Path) -> None:
        root = tree_root(tmp_path)
        os.symlink("no-such-target", root / "feeds" / "extra")
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.validate_candidate_tree(root)
        assert excinfo.value.code == "symlink_forbidden"

    def test_feed_symlink_rejected(self, tmp_path: Path) -> None:
        root = tree_root(tmp_path)
        (root / "feeds" / "rss.xml").unlink()
        os.symlink("../catalog.json", root / "feeds" / "rss.xml")
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.validate_candidate_tree(root)
        assert excinfo.value.code == "symlink_forbidden"

    def test_feed_fifo_rejected(self, tmp_path: Path) -> None:
        root = tree_root(tmp_path)
        os.mkfifo(root / "feeds" / "pipe")
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.validate_candidate_tree(root)
        assert excinfo.value.code == "special_file_forbidden"

    def test_extra_feed_basename_rejected(self, tmp_path: Path) -> None:
        root = tree_root(tmp_path)
        (root / "feeds" / "rss2.xml").write_bytes(b"x")
        with pytest.raises(pi.ProductIdentityError):
            pi.validate_candidate_tree(root)

    def test_case_variant_feed_rejected(self, tmp_path: Path) -> None:
        root = tree_root(tmp_path)
        probe = root / "feeds" / "cAsE.pRoBe"
        probe.write_bytes(b"x")
        case_sensitive = not (root / "feeds" / "case.probe").exists()
        probe.unlink()
        if not case_sensitive:
            pytest.skip("filesystem is case-insensitive; case variants alias fixed names")
        (root / "feeds" / "RSS.XML").write_bytes(b"x")
        with pytest.raises(pi.ProductIdentityError):
            pi.validate_candidate_tree(root)

    def test_nested_directory_in_feeds_rejected(self, tmp_path: Path) -> None:
        root = tree_root(tmp_path)
        (root / "feeds" / "sub").mkdir()
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.validate_candidate_tree(root)
        assert excinfo.value.code == "candidate_tree_invalid"

    def test_missing_feed_rejected(self, tmp_path: Path) -> None:
        root = tree_root(tmp_path)
        (root / "feeds" / "rss.xml").unlink()
        with pytest.raises(pi.ProductIdentityError):
            pi.validate_candidate_tree(root)

    def test_complete_tree_validated_before_any_copy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tree_root(tmp_path)
        (root / "feeds" / "extra.xml").write_bytes(b"x")
        opens: list[str] = []
        real_open = os.open

        def counting_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
            opens.append(str(path))
            return real_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(pi.os, "open", counting_open)
        with pytest.raises(pi.ProductIdentityError):
            pi.validate_candidate_tree(root)
        assert opens == []

    def test_toctou_inode_mismatch_is_violation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tree_root(tmp_path)
        real_fstat = os.fstat
        switch = {"armed": True}

        def racing_fstat(fd: int) -> os.stat_result:
            result = real_fstat(fd)
            if switch["armed"]:
                switch["armed"] = False
                return os.stat_result(
                    (
                        stat.S_IFREG | 0o644,
                        result.st_ino + 1,
                        result.st_dev + 1,
                        1,
                        0,
                        0,
                        0,
                        0,
                        0,
                        0,
                    )
                )
            return result

        monkeypatch.setattr(pi.os, "fstat", racing_fstat)
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.validate_candidate_tree(root)
        assert excinfo.value.code == "toctou_violation"


# --- module-level contract locks -------------------------------------------------


class TestContractConstants:
    def test_subject_order_frozen(self) -> None:
        assert pi.PRODUCT_SUBJECTS == (
            "catalog.json",
            "feeds/rss.xml",
            "feeds/atom.xml",
            "feeds/feed.json",
            "feeds/rss.simple.xml",
            "feeds/atom.simple.xml",
            "feeds/feed.simple.json",
        )

    def test_key_set_frozen_at_eleven(self) -> None:
        assert len(pi.IDENTITY_KEYS) == 11
        assert "observed_main_sha" not in pi.IDENTITY_KEYS

    def test_trailer_names_frozen(self) -> None:
        assert pi.UPDATE_RUN_ID_TRAILER == "Update-Run-Id"
        assert pi.UPDATE_RUN_ATTEMPT_TRAILER == "Update-Run-Attempt"

    def test_max_artifact_bytes_constant(self) -> None:
        assert pi.MAX_ARTIFACT_BYTES == 4096

    def test_allowed_actions(self) -> None:
        assert set(pi.ALLOWED_ACTIONS) == {"unchanged", "state_changed", "updated"}


# --- PID-REVIEW low-finding hardening (attempt 2) --------------------------------


class TestNoncanonicalSpellings:
    """Canonical-byte evasion spellings all fail the byte-compare layer."""

    @staticmethod
    def _text() -> str:
        return doc_bytes(valid_doc()).decode("utf-8")

    def test_unicode_escape_repository_rejected(self) -> None:
        text = self._text().replace(
            f'"{pi.REPOSITORY}"', '"wy\\u0061ttowalsh/paul-graham-essay-feeds"'
        )
        assert "wy\\u0061ttowalsh" in text  # escape spelling present, value identical
        rejects(text.encode("utf-8"), REST_UPDATED, "noncanonical_bytes")

    def test_leading_and_trailing_whitespace_rejected(self) -> None:
        data = b" " + doc_bytes(valid_doc()) + b"\t"
        rejects(data, REST_UPDATED, "noncanonical_bytes")

    def test_negative_zero_attempt_rejected(self) -> None:
        text = self._text().replace('"workflow_run_attempt": 1', '"workflow_run_attempt": -0')
        rejects(text.encode("utf-8"), REST_UPDATED, "invalid_value")

    def test_exponent_run_id_rejected(self) -> None:
        text = self._text().replace(
            '"workflow_run_id": 123456789', '"workflow_run_id": 123456789e0'
        )
        rejects(text.encode("utf-8"), REST_UPDATED, "wrong_type")

    def test_surrogate_escape_payload_fails_closed(self) -> None:
        text = self._text().replace(f'"{_SHA1}"', '"\\ud800' + "c" * 39 + '"')
        rejects(text.encode("utf-8"), REST_UPDATED, "noncanonical_bytes")


class TestCommitMessageDecoding:
    def test_non_utf8_commit_message_fails_closed(self, tmp_path: Path) -> None:
        repo, source = make_base_repo(tmp_path)
        tree = _git(repo, "rev-parse", f"{source}^{{tree}}").stdout.strip()
        raw_commit = (
            f"tree {tree}\n"
            f"parent {source}\n"
            "author T <t@example.com> 1700000000 +0000\n"
            "committer T <t@example.com> 1700000000 +0000\n"
            "\n"
        ).encode("ascii") + b"subject with \xfe raw bytes\n"
        child = (
            subprocess.run(
                ["git", "-C", str(repo), "hash-object", "-t", "commit", "-w", "--stdin"],
                input=raw_commit,
                capture_output=True,
                check=True,
            )
            .stdout.decode("ascii")
            .strip()
        )
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.commit_trailers(repo, child)
        assert excinfo.value.code == "commit_unreadable"


class TestDiffUndecodablePath:
    def test_raw_byte_path_fails_closed(self, tmp_path: Path) -> None:
        repo, source = make_base_repo(tmp_path)
        blob = (
            subprocess.run(
                ["git", "-C", str(repo), "hash-object", "-w", "--stdin"],
                input=b"x",
                capture_output=True,
                check=True,
            )
            .stdout.decode("ascii")
            .strip()
        )
        bad_path = os.fsdecode(b"bad\xfe.xml")
        _git(repo, "update-index", "--add", "--cacheinfo", f"100644,{blob},{bad_path}")
        _git(repo, "commit", "-q", "-m", "raw path bytes")
        child = _git(repo, "rev-parse", "HEAD").stdout.strip()
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.diff_entries(repo, source, child)
        assert excinfo.value.code == "non_product_path"


class TestPushArgvForceSpellings:
    @pytest.mark.parametrize(
        "flag",
        ["--force=x", "-f", "--force-with-lease=main:abc", "+refs/heads/main:refs/heads/main"],
    )
    def test_value_suffixed_force_spellings_forbidden(self, flag: str) -> None:
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.validate_push_argv(["git", "push", flag, "origin", f"{_SHA1}:refs/heads/main"])
        assert excinfo.value.code == "force_push_forbidden"


class TestRestMetadataValidation:
    @pytest.mark.parametrize(
        ("run_id", "run_attempt"),
        [
            (True, RUN_ATTEMPT),
            (RUN_ID, True),
            (1.0, RUN_ATTEMPT),
            (RUN_ID, 1.0),
            ("123", RUN_ATTEMPT),
        ],
    )
    def test_non_exact_int_run_identity_rejected(self, run_id: Any, run_attempt: Any) -> None:
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.RestMetadata(run_id, run_attempt, pi.REPOSITORY, "updated")
        assert excinfo.value.code == "rest_metadata_invalid"

    def test_non_positive_run_identity_rejected(self) -> None:
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.RestMetadata(0, RUN_ATTEMPT, pi.REPOSITORY, "updated")
        assert excinfo.value.code == "rest_metadata_invalid"

    def test_untrusted_repository_rejected(self) -> None:
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.RestMetadata(RUN_ID, RUN_ATTEMPT, "someone/else", "updated")
        assert excinfo.value.code == "rest_metadata_invalid"

    def test_disallowed_action_rejected(self) -> None:
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.RestMetadata(RUN_ID, RUN_ATTEMPT, pi.REPOSITORY, "deleted")
        assert excinfo.value.code == "rest_metadata_invalid"

    def test_valid_record_accepted(self) -> None:
        record = pi.RestMetadata(RUN_ID, RUN_ATTEMPT, pi.REPOSITORY, "updated")
        assert record.run_id == RUN_ID


class TestRemoteNameValidation:
    @pytest.mark.parametrize(
        "remote", ["../evil", "origin --upload-pack=x", "", "a b", "-o", "o\nx"]
    )
    def test_bad_remote_names_rejected(self, remote: str) -> None:
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.build_fetch_argv(_SHA1, remote=remote)
        assert excinfo.value.code == "invalid_remote"

    def test_plain_remote_accepted(self) -> None:
        assert pi.build_fetch_argv(_SHA1, remote="upstream-2") == [
            "git",
            "fetch",
            "--no-tags",
            "upstream-2",
            _SHA1,
        ]

    def test_fetch_rejects_bad_remote_before_fetch(self, tmp_path: Path) -> None:
        repo, _source = make_base_repo(tmp_path)
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.fetch_exact_commit(repo, _SHA1, remote="origin --upload-pack=evil")
        assert excinfo.value.code == "invalid_remote"


class TestPlumbingOidGrammar:
    @pytest.mark.parametrize(
        "call",
        [
            lambda repo, oid: pi.require_sufficient_history(repo, oid),
            lambda repo, oid: pi.parent_oids(repo, oid),
            lambda repo, oid: pi.product_tree_entries(repo, oid),
            lambda repo, oid: pi.commit_trailers(repo, oid),
            lambda repo, oid: pi.diff_entries(repo, oid, oid),
        ],
        ids=["sufficient-history", "parents", "tree-entries", "trailers", "diff"],
    )
    def test_ref_spellings_rejected(self, tmp_path: Path, call: Any) -> None:
        repo, _source = make_base_repo(tmp_path)
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            call(repo, "HEAD")
        assert excinfo.value.code == "invalid_oid"


class TestStderrSanitization:
    def test_first_line_only(self) -> None:
        sanitized = pi._sanitize_stderr("fatal: first\nfatal: second\n")
        assert sanitized == "fatal: first"

    def test_urls_redacted(self) -> None:
        sanitized = pi._sanitize_stderr(
            "fatal: Authentication failed for 'https://token@github.com/owner/repo/'"
        )
        assert "https://" not in sanitized
        assert "token" not in sanitized
        assert "github.com" not in sanitized
        assert "[redacted-url]" in sanitized

    def test_long_lines_truncated_to_200(self) -> None:
        assert len(pi._sanitize_stderr("x" * 5000)) == 200

    def test_empty_stderr(self) -> None:
        assert pi._sanitize_stderr("") == ""
        assert pi._sanitize_stderr("   \n  ") == ""


class TestFeedsSwapHardening:
    def test_feeds_replaced_by_symlink_rejected(self, tmp_path: Path) -> None:
        root = tree_root(tmp_path)
        assert pi.validate_candidate_tree(root) == BASE_FILES
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        shutil.rmtree(root / "feeds")
        os.symlink(elsewhere, root / "feeds")
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.validate_candidate_tree(root)
        assert excinfo.value.code == "symlink_forbidden"

    def test_feeds_identity_swap_between_scan_and_open_detected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tree_root(tmp_path)
        real_open = os.open
        armed = {"swap": True}

        def swapping_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
            if armed["swap"] and os.fspath(path) == "feeds":
                armed["swap"] = False
                decoy = tmp_path / "decoy"
                decoy.mkdir()
                shutil.rmtree(root / "feeds")
                os.rename(decoy, root / "feeds")
            return real_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(pi.os, "open", swapping_open)
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.validate_candidate_tree(root)
        assert excinfo.value.code == "toctou_violation"


# --- path-based artifact file + CI authorization --------------------------------


class TestArtifactFile:
    def test_reads_regular_file(self, tmp_path: Path) -> None:
        path = tmp_path / "product-identity.json"
        payload = doc_bytes(valid_doc())
        path.write_bytes(payload)
        assert pi.validate_artifact_file(path) == payload
        identity = pi.validate_identity_file(path, REST_UPDATED, object_format="sha1")
        assert identity.product_sha == _SHA1

    def test_rejects_missing(self, tmp_path: Path) -> None:
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.validate_artifact_file(tmp_path / "absent.json")
        assert excinfo.value.code == "artifact_file_invalid"

    def test_rejects_symlink(self, tmp_path: Path) -> None:
        real = tmp_path / "real.json"
        real.write_bytes(doc_bytes(valid_doc()))
        link = tmp_path / "link.json"
        os.symlink(real, link)
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.validate_artifact_file(link)
        assert excinfo.value.code == "symlink_forbidden"

    def test_rejects_directory(self, tmp_path: Path) -> None:
        target = tmp_path / "dir"
        target.mkdir()
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.validate_artifact_file(target)
        assert excinfo.value.code == "artifact_not_file"

    def test_rejects_oversized_file(self, tmp_path: Path) -> None:
        path = tmp_path / "big.json"
        path.write_bytes(b"x" * (pi.MAX_ARTIFACT_BYTES + 1))
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.validate_artifact_file(path)
        assert excinfo.value.code == "artifact_too_large"

    def test_toctou_inode_mismatch(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = tmp_path / "product-identity.json"
        path.write_bytes(doc_bytes(valid_doc()))
        real_fstat = os.fstat

        def racing_fstat(fd: int) -> os.stat_result:
            result = real_fstat(fd)
            return os.stat_result(
                (
                    stat.S_IFREG | 0o644,
                    result.st_ino + 1,
                    result.st_dev + 1,
                    1,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                )
            )

        monkeypatch.setattr(pi.os, "fstat", racing_fstat)
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.validate_artifact_file(path)
        assert excinfo.value.code == "toctou_violation"


class TestCiAuthorization:
    _CI_RUN = 123456790
    _CI_ATTEMPT = 2
    _WF_ID = 312025551
    _ART_ID = 98765432
    _ART_DIGEST = "sha256:" + "b" * 64
    _WF_SHA = "e" * 40

    def _push_kwargs(self) -> dict[str, Any]:
        return {
            "ci_event": "push",
            "ci_head_sha": _SHA1,
            "ci_run_attempt": self._CI_ATTEMPT,
            "ci_run_id": self._CI_RUN,
            "ci_workflow_id": self._WF_ID,
            "ci_workflow_sha": self._WF_SHA,
            "product_digest": _DIGEST,
            "product_identity_artifact_digest": None,
            "product_identity_artifact_id": None,
            "route": "push",
            "selected_product_sha": _SHA1,
            "update_action": None,
            "update_run_attempt": None,
            "update_run_id": None,
            "update_source_sha": None,
            "object_format": "sha1",
        }

    def _continuation_kwargs(self) -> dict[str, Any]:
        return {
            "ci_event": "workflow_run",
            "ci_head_sha": _OTHER_SHA1,
            "ci_run_attempt": self._CI_ATTEMPT,
            "ci_run_id": self._CI_RUN,
            "ci_workflow_id": self._WF_ID,
            "ci_workflow_sha": self._WF_SHA,
            "product_digest": _DIGEST,
            "product_identity_artifact_digest": self._ART_DIGEST,
            "product_identity_artifact_id": self._ART_ID,
            "route": "update_continuation",
            "selected_product_sha": _SHA1,
            "update_action": "updated",
            "update_run_attempt": RUN_ATTEMPT,
            "update_run_id": RUN_ID,
            "update_source_sha": _OTHER_SHA1,
            "object_format": "sha1",
        }

    def test_key_set_frozen_at_nineteen(self) -> None:
        assert len(pi.CI_AUTHORIZATION_KEYS) == 19
        assert "observed_main_sha" not in pi.CI_AUTHORIZATION_KEYS

    def test_push_route_roundtrip(self) -> None:
        data = pi.build_ci_authorization_document(**self._push_kwargs())
        auth = pi.validate_ci_authorization_document(data, object_format="sha1")
        assert auth.route == "push"
        assert auth.ci_event == "push"
        assert auth.ci_head_sha == auth.selected_product_sha == _SHA1
        assert auth.update_action is None
        assert auth.product_identity_artifact_id is None
        assert auth.gate_result == "passed"
        assert data.endswith(b"\n")
        keys = [
            ln.strip().split(":")[0].strip('"')
            for ln in data.decode().splitlines()
            if ln.startswith('  "')
        ]
        assert keys == sorted(keys)
        assert set(keys) == set(pi.CI_AUTHORIZATION_KEYS)

    def test_update_continuation_roundtrip(self) -> None:
        data = pi.build_ci_authorization_document(**self._continuation_kwargs())
        auth = pi.validate_ci_authorization_document(data, object_format="sha1")
        assert auth.route == "update_continuation"
        assert auth.ci_event == "workflow_run"
        assert auth.update_run_id == RUN_ID
        assert auth.product_identity_artifact_id == self._ART_ID

    def test_push_rejects_non_null_update_fields(self) -> None:
        kwargs = self._push_kwargs()
        kwargs["update_action"] = "updated"
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.build_ci_authorization_document(**kwargs)
        assert excinfo.value.code == "ci_route_invariant"

    def test_continuation_rejects_null_identity_fields(self) -> None:
        kwargs = self._continuation_kwargs()
        kwargs["update_run_id"] = None
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.build_ci_authorization_document(**kwargs)
        assert excinfo.value.code == "ci_route_invariant"

    def test_push_requires_head_equals_selected(self) -> None:
        kwargs = self._push_kwargs()
        kwargs["ci_head_sha"] = _OTHER_SHA1
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.build_ci_authorization_document(**kwargs)
        assert excinfo.value.code == "ci_route_invariant"

    def test_gate_result_must_be_passed(self) -> None:
        kwargs = self._push_kwargs()
        kwargs["gate_result"] = "unchanged"
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.build_ci_authorization_document(**kwargs)
        assert excinfo.value.code == "invalid_gate_result"

    def test_wrong_workflow_path_rejected(self) -> None:
        kwargs = self._push_kwargs()
        kwargs["ci_workflow_path"] = ".github/workflows/update-feeds.yml"
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.build_ci_authorization_document(**kwargs)
        assert excinfo.value.code == "invalid_ci_workflow_path"

    def test_compact_bytes_rejected(self) -> None:
        data = pi.build_ci_authorization_document(**self._push_kwargs())
        compact = json.dumps(json.loads(data), sort_keys=True).encode()
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.validate_ci_authorization_document(compact, object_format="sha1")
        assert excinfo.value.code == "noncanonical_bytes"

    def test_bool_artifact_id_rejected(self) -> None:
        kwargs = self._continuation_kwargs()
        payload = json.loads(pi.build_ci_authorization_document(**kwargs))
        payload["product_identity_artifact_id"] = True
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.validate_ci_authorization_document(
                pi.canonical_json_bytes(payload), object_format="sha1"
            )
        assert excinfo.value.code == "wrong_type"

    def test_bind_requires_published_identity(self) -> None:
        auth = pi.validate_ci_authorization_document(
            pi.build_ci_authorization_document(**self._continuation_kwargs()),
            object_format="sha1",
        )
        unpublished = pi.validate_identity_document(doc_bytes(valid_doc()), REST_UPDATED)
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.bind_ci_authorization_to_identity(auth, unpublished)
        assert excinfo.value.code == "not_published"

    def test_bind_matches_published_identity(self) -> None:
        data = pi.build_identity_document(
            action="updated",
            candidate_digest=_DIGEST,
            committed=True,
            product_sha=_SHA1,
            repository=pi.REPOSITORY,
            source_sha=_OTHER_SHA1,
            workflow_run_attempt=RUN_ATTEMPT,
            workflow_run_id=RUN_ID,
            origin_main=_SHA1,
            object_format="sha1",
        )
        identity = pi.validate_identity_document(data, REST_UPDATED, object_format="sha1")
        auth = pi.validate_ci_authorization_document(
            pi.build_ci_authorization_document(**self._continuation_kwargs()),
            object_format="sha1",
        )
        pi.bind_ci_authorization_to_identity(auth, identity)
        pi.require_authorizing_identity(identity)

    def test_bind_rejects_product_sha_mismatch(self) -> None:
        kwargs = self._continuation_kwargs()
        kwargs["selected_product_sha"] = "a" * 40
        auth = pi.validate_ci_authorization_document(
            pi.build_ci_authorization_document(**kwargs),
            object_format="sha1",
        )
        data = pi.build_identity_document(
            action="updated",
            candidate_digest=_DIGEST,
            committed=True,
            product_sha=_SHA1,
            repository=pi.REPOSITORY,
            source_sha=_OTHER_SHA1,
            workflow_run_attempt=RUN_ATTEMPT,
            workflow_run_id=RUN_ID,
            origin_main=_SHA1,
            object_format="sha1",
        )
        identity = pi.validate_identity_document(data, REST_UPDATED, object_format="sha1")
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.bind_ci_authorization_to_identity(auth, identity)
        assert excinfo.value.code == "ci_identity_mismatch"

    def test_artifact_name_template(self) -> None:
        assert (
            pi.expected_ci_authorization_artifact_name(
                self._CI_RUN, self._CI_ATTEMPT, "push", _SHA1
            )
            == f"ci-authorization-{self._CI_RUN}-{self._CI_ATTEMPT}-push-{_SHA1}"
        )
        assert pi.expected_ci_noop_artifact_name(RUN_ID, RUN_ATTEMPT) == (
            f"ci-noop-{RUN_ID}-{RUN_ATTEMPT}.json"
        )

    def test_select_ci_authorization_artifact(self) -> None:
        name = pi.expected_ci_authorization_artifact_name(
            self._CI_RUN, self._CI_ATTEMPT, "push", _SHA1
        )
        record = pi.ArtifactRecord(
            artifact_id=7,
            name=name,
            expired=False,
            archive_digest="sha256:" + "0" * 64,
        )
        selected = pi.select_ci_authorization_artifact(
            [record],
            ci_run_id=self._CI_RUN,
            ci_run_attempt=self._CI_ATTEMPT,
            route="push",
            selected_sha=_SHA1,
        )
        assert selected.artifact_id == 7

    def test_validate_from_path(self, tmp_path: Path) -> None:
        data = pi.build_ci_authorization_document(**self._push_kwargs())
        path = tmp_path / "ci-authorization.json"
        path.write_bytes(data)
        auth = pi.validate_ci_authorization_file(path, object_format="sha1")
        assert auth.route == "push"

    def test_resolve_workflow_id_by_path(self) -> None:
        workflows = [
            {
                "id": 312025553,
                "name": "Update feeds",
                "path": ".github/workflows/update-feeds.yml",
                "state": "active",
            },
            {
                "id": 312025551,
                "name": "CI",
                "path": ".github/workflows/ci.yml",
                "state": "active",
            },
        ]
        assert (
            pi.resolve_active_workflow_id(
                workflows, path=pi.CI_WORKFLOW_PATH, name=pi.CI_WORKFLOW_NAME
            )
            == 312025551
        )

    def test_resolve_workflow_id_rejects_inactive_or_wrong_name(self) -> None:
        with pytest.raises(pi.ProductIdentityError) as excinfo:
            pi.resolve_active_workflow_id(
                [{"id": 1, "name": "CI", "path": pi.CI_WORKFLOW_PATH, "state": "disabled"}],
                path=pi.CI_WORKFLOW_PATH,
                name=pi.CI_WORKFLOW_NAME,
            )
        assert excinfo.value.code == "workflow_resolution"
        with pytest.raises(pi.ProductIdentityError):
            pi.resolve_active_workflow_id(
                [{"id": 1, "name": "Other", "path": pi.CI_WORKFLOW_PATH, "state": "active"}],
                path=pi.CI_WORKFLOW_PATH,
                name=pi.CI_WORKFLOW_NAME,
            )


class TestCli:
    def test_constants(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert pi.main(["constants"]) == 0
        out = capsys.readouterr().out
        assert "schema_version=1" in out
        assert pi.REPOSITORY in out
        assert pi.CI_WORKFLOW_PATH in out

    def test_validate_identity(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        path = tmp_path / "product-identity.json"
        path.write_bytes(doc_bytes(valid_doc()))
        rc = pi.main(
            [
                "validate",
                "identity",
                str(path),
                f"--run-id={RUN_ID}",
                f"--run-attempt={RUN_ATTEMPT}",
                "--action=updated",
                "--object-format=sha1",
            ]
        )
        assert rc == 0
        assert "product_sha=" in capsys.readouterr().out

    def test_validate_ci_authorization(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        data = pi.build_ci_authorization_document(
            ci_event="push",
            ci_head_sha=_SHA1,
            ci_run_attempt=2,
            ci_run_id=123456790,
            ci_workflow_id=312025551,
            ci_workflow_sha="e" * 40,
            product_digest=_DIGEST,
            product_identity_artifact_digest=None,
            product_identity_artifact_id=None,
            route="push",
            selected_product_sha=_SHA1,
            update_action=None,
            update_run_attempt=None,
            update_run_id=None,
            update_source_sha=None,
            object_format="sha1",
        )
        path = tmp_path / "ci-authorization.json"
        path.write_bytes(data)
        assert pi.main(["validate", "ci-authorization", str(path)]) == 0
        assert "route=push" in capsys.readouterr().out

    def test_usage_exit_2(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert pi.main([]) == 2
        assert "usage:" in capsys.readouterr().err

    def test_validate_rejects_symlink(self, tmp_path: Path) -> None:
        real = tmp_path / "real.json"
        real.write_bytes(doc_bytes(valid_doc()))
        link = tmp_path / "link.json"
        os.symlink(real, link)
        rc = pi.main(
            [
                "validate",
                "identity",
                str(link),
                f"--run-id={RUN_ID}",
                f"--run-attempt={RUN_ATTEMPT}",
                "--action=updated",
            ]
        )
        assert rc == 1
