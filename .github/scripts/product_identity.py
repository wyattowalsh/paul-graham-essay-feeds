"""Shared ProductIdentity contract helpers (stdlib-only).

This module is the single trusted implementation behind the Update feeds
producer, the CI/Verify product consumers, and the Pages reconciler. It
implements the v1 ProductIdentity schema, canonical JSON framing, strict git
object-OID grammar, commit-action classification, candidate digests over the
seven durable product subjects, push reconciliation, run-scoped artifact
selection, exact-OID object acquisition, and physical candidate-tree
validation described in the remediation plan (sections 2.1-2.3).

Only the Python standard library is ever imported here; privileged workflow
steps load this file directly from a validated checkout.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

# --- Frozen contract constants ------------------------------------------------

SCHEMA_VERSION: Final[int] = 1
REPOSITORY: Final[str] = "wyattowalsh/paul-graham-essay-feeds"

#: The seven durable product subjects in fixed digest order.
PRODUCT_SUBJECTS: Final[tuple[str, ...]] = (
    "catalog.json",
    "feeds/rss.xml",
    "feeds/atom.xml",
    "feeds/feed.json",
    "feeds/rss.simple.xml",
    "feeds/atom.simple.xml",
    "feeds/feed.simple.json",
)

#: The six fixed feed basenames under ``feeds/``.
FEED_BASENAMES: Final[frozenset[str]] = frozenset(
    subject.split("/", 1)[1] for subject in PRODUCT_SUBJECTS if "/" in subject
)

#: Exact key set of the ProductIdentity schema (11 keys, no more, no fewer).
IDENTITY_KEYS: Final[frozenset[str]] = frozenset(
    {
        "action",
        "candidate_digest",
        "committed",
        "product_sha",
        "published",
        "repository",
        "schema_version",
        "source_sha",
        "subjects",
        "workflow_run_attempt",
        "workflow_run_id",
    }
)

ALLOWED_ACTIONS: Final[tuple[str, ...]] = ("unchanged", "state_changed", "updated")

#: Maximum accepted ProductIdentity artifact payload size in bytes.
MAX_ARTIFACT_BYTES: Final[int] = 4096

#: Bounded retry count for a not-applied product push (one retry maximum).
MAX_PUSH_RETRIES: Final[int] = 1

#: Commit-message trailer names binding a product commit to its Update run.
UPDATE_RUN_ID_TRAILER: Final[str] = "Update-Run-Id"
UPDATE_RUN_ATTEMPT_TRAILER: Final[str] = "Update-Run-Attempt"

GIT_TIMEOUT_SECONDS: Final[float] = 120.0

#: Explicit error emitted when validation would otherwise mis-read shallow,
#: depth-limited history instead of failing closed.
INSUFFICIENT_HISTORY_MESSAGE: Final[str] = "insufficient history; fetch exact OIDs"

#: Trusted workflow identities resolved by exact path at runtime (IDs are
#: execution evidence, never hardcoded permanent roots).
UPDATE_WORKFLOW_NAME: Final[str] = "Update feeds"
UPDATE_WORKFLOW_PATH: Final[str] = ".github/workflows/update-feeds.yml"
CI_WORKFLOW_NAME: Final[str] = "CI"
CI_WORKFLOW_PATH: Final[str] = ".github/workflows/ci.yml"
PAGES_WORKFLOW_NAME: Final[str] = "Pages"
PAGES_WORKFLOW_PATH: Final[str] = ".github/workflows/pages.yml"
VERIFY_WORKFLOW_NAME: Final[str] = "Verify product"
VERIFY_WORKFLOW_PATH: Final[str] = ".github/workflows/verify-product.yml"

#: Exact key set of canonical ``ci-authorization.json`` (19 keys).
CI_AUTHORIZATION_KEYS: Final[frozenset[str]] = frozenset(
    {
        "ci_event",
        "ci_head_sha",
        "ci_run_attempt",
        "ci_run_id",
        "ci_workflow_id",
        "ci_workflow_path",
        "ci_workflow_sha",
        "gate_result",
        "product_digest",
        "product_identity_artifact_digest",
        "product_identity_artifact_id",
        "repository",
        "route",
        "schema_version",
        "selected_product_sha",
        "update_action",
        "update_run_attempt",
        "update_run_id",
        "update_source_sha",
    }
)

#: Route-dependent fields that are JSON null on ``push`` and non-null on
#: ``update_continuation``. Spec §2.4 groups ``update_*`` / ``product_identity_*``.
CI_ROUTE_NULLABLE_KEYS: Final[tuple[str, ...]] = (
    "product_identity_artifact_digest",
    "product_identity_artifact_id",
    "update_action",
    "update_run_attempt",
    "update_run_id",
    "update_source_sha",
)

ALLOWED_CI_ROUTES: Final[tuple[str, ...]] = ("push", "update_continuation")
ALLOWED_CI_EVENTS: Final[tuple[str, ...]] = ("push", "workflow_run")
GATE_RESULT_PASSED: Final[str] = "passed"

_PUSH_OUTCOMES: Final[frozenset[str]] = frozenset(
    {"command_ok", "command_error", "command_timeout"}
)
_HEX_RE: Final[re.Pattern[str]] = re.compile(r"\A[0-9a-f]+\Z")
_DIGEST_RE: Final[re.Pattern[str]] = re.compile(r"\Asha256:[0-9a-f]{64}\Z")
_REMOTE_RE: Final[re.Pattern[str]] = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_URL_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://\S+")
_OID_LENGTHS: Final[dict[str, int]] = {"sha1": 40, "sha256": 64}


class ProductIdentityError(Exception):
    """Raised for every ProductIdentity contract violation."""

    def __init__(self, message: str, *, code: str = "violation") -> None:
        super().__init__(message)
        self.code = code


# --- Injected trusted REST metadata record ------------------------------------


@dataclass(frozen=True)
class RestMetadata:
    """Fresh authenticated REST metadata for the producing Update run.

    This is an injected record parameter: workflow steps build it from freshly
    refetched REST state (never from event payloads alone) and pass it in.
    """

    run_id: int
    run_attempt: int
    repository: str
    action: str

    def __post_init__(self) -> None:
        if type(self.run_id) is not int or type(self.run_attempt) is not int:
            msg = "run_id and run_attempt must be exact ints (never bool or float)"
            raise ProductIdentityError(msg, code="rest_metadata_invalid")
        if self.run_id <= 0 or self.run_attempt <= 0:
            msg = "run_id and run_attempt must be positive"
            raise ProductIdentityError(msg, code="rest_metadata_invalid")
        if not isinstance(self.repository, str) or self.repository != REPOSITORY:
            msg = "repository must be the trusted repository name"
            raise ProductIdentityError(msg, code="rest_metadata_invalid")
        if self.action not in ALLOWED_ACTIONS:
            msg = f"action must be one of {ALLOWED_ACTIONS}"
            raise ProductIdentityError(msg, code="rest_metadata_invalid")


@dataclass(frozen=True)
class ArtifactRecord:
    """Minimal artifact metadata fields required by the selection contract."""

    artifact_id: int
    name: str
    expired: bool
    archive_digest: str


@dataclass(frozen=True)
class DiffEntry:
    """One raw ``git diff --raw --no-renames`` record."""

    status: str
    path: str
    old_mode: str
    new_mode: str
    old_oid: str
    new_oid: str


@dataclass(frozen=True)
class TreeEntry:
    """One raw ``git ls-tree`` record."""

    mode: str
    kind: str
    oid: str
    path: str


@dataclass(frozen=True)
class ProductIdentity:
    """Validated ProductIdentity record (schema v1, 11 fields)."""

    action: str
    candidate_digest: str
    committed: bool
    product_sha: str
    published: bool
    repository: str
    schema_version: int
    source_sha: str
    subjects: tuple[str, ...]
    workflow_run_attempt: int
    workflow_run_id: int

    def to_dict(self) -> dict[str, object]:
        """Serialize to the exact schema key set."""
        return {
            "action": self.action,
            "candidate_digest": self.candidate_digest,
            "committed": self.committed,
            "product_sha": self.product_sha,
            "published": self.published,
            "repository": self.repository,
            "schema_version": self.schema_version,
            "source_sha": self.source_sha,
            "subjects": list(self.subjects),
            "workflow_run_attempt": self.workflow_run_attempt,
            "workflow_run_id": self.workflow_run_id,
        }

    def to_bytes(self) -> bytes:
        """Canonical JSON serialization used by the writer."""
        return canonical_json_bytes(self.to_dict())


# --- Canonical JSON -----------------------------------------------------------


def canonical_json_text(obj: object) -> str:
    """Return the canonical JSON text: sorted keys, 2-space indent, final LF."""
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def canonical_json_bytes(obj: object) -> bytes:
    """Return the canonical UTF-8 JSON bytes of ``obj``."""
    return canonical_json_text(obj).encode("utf-8")


def _reject_duplicate_keys(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            msg = f"duplicate JSON object key: {key!r}"
            raise ProductIdentityError(msg, code="json_duplicate_key")
        result[key] = value
    return result


def _reject_constant(name: str) -> object:
    msg = f"non-finite JSON constant: {name}"
    raise ProductIdentityError(msg, code="json_non_finite")


def parse_strict_json(data: bytes) -> object:
    """Strictly parse canonical-JSON bytes.

    Rejects a UTF-8 BOM, undecodable bytes, duplicate object keys, the
    non-finite constants ``NaN``/``Infinity``/``-Infinity``, and any trailing
    data after the top-level JSON value.
    """
    if data.startswith(b"\xef\xbb\xbf"):
        msg = "JSON payload starts with a UTF-8 BOM"
        raise ProductIdentityError(msg, code="json_bom")
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        msg = f"JSON payload is not strict UTF-8: {exc}"
        raise ProductIdentityError(msg, code="json_invalid_utf8") from exc
    if text.startswith("\ufeff"):
        msg = "JSON payload starts with a U+FEFF character"
        raise ProductIdentityError(msg, code="json_bom") from None
    try:
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except ProductIdentityError:
        raise
    except ValueError as exc:
        msg = f"JSON payload is not a single strict JSON value: {exc}"
        raise ProductIdentityError(msg, code="json_invalid") from exc


# --- Identity document validation and construction ---------------------------


def _require_exact_type(value: object, expected: type, name: str) -> object:
    # ``type(...) is`` (not isinstance) so bool never substitutes for int and
    # float never substitutes for int.
    if type(value) is not expected:
        msg = f"{name} must be exactly {expected.__name__}, got {type(value).__name__}"
        raise ProductIdentityError(msg, code="wrong_type")
    return value


def _require_positive_int(value: object, name: str) -> int:
    number = _require_exact_type(value, int, name)
    if number <= 0:
        msg = f"{name} must be a positive integer, got {number}"
        raise ProductIdentityError(msg, code="invalid_value")
    return number


def validate_oid_text(oid: object, object_format: str) -> str:
    """Validate a git object OID by grammar alone (no subprocess).

    Accepts exactly 40 lowercase hex characters for ``sha1`` storage and
    exactly 64 for ``sha256``. Refuse-listed spellings (abbreviations,
    uppercase, whitespace, ref names such as ``main``, ``HEAD``, ``HEAD~1``,
    ``refs/...``, ``@``, and any OID-shaped injection string) all fail this
    full-string grammar before any git command is constructed.
    """
    expected_len = _OID_LENGTHS.get(object_format)
    if expected_len is None:
        msg = f"unsupported git object format: {object_format!r}"
        raise ProductIdentityError(msg, code="unsupported_object_format")
    if not isinstance(oid, str):
        msg = f"OID must be a str, got {type(oid).__name__}"
        raise ProductIdentityError(msg, code="invalid_oid")
    if len(oid) != expected_len:
        msg = f"OID must be exactly {expected_len} characters for {object_format}"
        raise ProductIdentityError(msg, code="invalid_oid")
    if _HEX_RE.match(oid) is None:
        msg = "OID must be lowercase hexadecimal with no ref syntax or whitespace"
        raise ProductIdentityError(msg, code="invalid_oid")
    return oid


def validate_digest_text(digest: object) -> str:
    """Validate a ``sha256:<64 lowercase hex>`` digest string."""
    if not isinstance(digest, str) or _DIGEST_RE.match(digest) is None:
        msg = "digest must match sha256:<64 lowercase hex>"
        raise ProductIdentityError(msg, code="invalid_digest")
    return digest


def validate_remote_name(remote: object) -> str:
    """Validate a git remote name by grammar alone (no subprocess)."""
    if not isinstance(remote, str) or _REMOTE_RE.match(remote) is None:
        msg = "remote name must match [A-Za-z0-9._-]+"
        raise ProductIdentityError(msg, code="invalid_remote")
    return remote


def validate_identity_document(
    data: bytes,
    rest: RestMetadata,
    *,
    object_format: str | None = None,
) -> ProductIdentity:
    """Validate a ProductIdentity artifact payload end to end.

    Checks the size ceiling, strict canonical JSON (byte-for-byte), the exact
    11-key set, exact types (bools never substitute for ints, no floats), the
    fixed subject order, action and repository agreement with the injected
    REST metadata record, positive run id/attempt agreement, digest grammar,
    OID grammar (when ``object_format`` is supplied), and action
    self-consistency.
    """
    if len(data) > MAX_ARTIFACT_BYTES:
        msg = f"identity payload exceeds {MAX_ARTIFACT_BYTES} bytes"
        raise ProductIdentityError(msg, code="artifact_too_large")
    obj = parse_strict_json(data)
    if not isinstance(obj, dict):
        msg = "identity payload must be a JSON object"
        raise ProductIdentityError(msg, code="json_not_object")
    keys = set(obj)
    missing = sorted(IDENTITY_KEYS - keys)
    extra = sorted(keys - IDENTITY_KEYS)
    if missing or extra:
        msg = f"identity key set mismatch: missing={missing} extra={extra}"
        raise ProductIdentityError(msg, code="key_set_mismatch")

    schema_version = _require_exact_type(obj["schema_version"], int, "schema_version")
    if schema_version != SCHEMA_VERSION:
        msg = f"schema_version must be {SCHEMA_VERSION}, got {schema_version}"
        raise ProductIdentityError(msg, code="invalid_value")

    action = obj["action"]
    if not isinstance(action, str) or action not in ALLOWED_ACTIONS:
        msg = f"action must be one of {ALLOWED_ACTIONS}"
        raise ProductIdentityError(msg, code="invalid_action")
    if action != rest.action:
        msg = f"action {action!r} does not match REST metadata {rest.action!r}"
        raise ProductIdentityError(msg, code="rest_metadata_mismatch")

    repository = obj["repository"]
    if repository != REPOSITORY or repository != rest.repository:
        msg = f"repository {repository!r} does not match trusted context"
        raise ProductIdentityError(msg, code="rest_metadata_mismatch")

    run_id = _require_positive_int(obj["workflow_run_id"], "workflow_run_id")
    run_attempt = _require_positive_int(obj["workflow_run_attempt"], "workflow_run_attempt")
    if run_id != rest.run_id or run_attempt != rest.run_attempt:
        msg = (
            f"run identity ({run_id}, {run_attempt}) does not match REST metadata "
            f"({rest.run_id}, {rest.run_attempt})"
        )
        raise ProductIdentityError(msg, code="rest_metadata_mismatch")

    subjects = obj["subjects"]
    if not isinstance(subjects, list) or tuple(subjects) != PRODUCT_SUBJECTS:
        msg = "subjects must be exactly the seven fixed names in fixed order"
        raise ProductIdentityError(msg, code="invalid_subjects")

    candidate_digest = validate_digest_text(obj["candidate_digest"])
    committed = _require_exact_type(obj["committed"], bool, "committed")
    published = _require_exact_type(obj["published"], bool, "published")

    product_sha = obj["product_sha"]
    source_sha = obj["source_sha"]
    if object_format is not None:
        validate_oid_text(product_sha, object_format)
        validate_oid_text(source_sha, object_format)
    elif not isinstance(product_sha, str) or not isinstance(source_sha, str):
        msg = "product_sha and source_sha must be strings"
        raise ProductIdentityError(msg, code="invalid_oid")

    if action == "unchanged":
        if committed:
            msg = "unchanged requires committed=false"
            raise ProductIdentityError(msg, code="action_inconsistent")
        if product_sha != source_sha:
            msg = "unchanged requires product_sha == source_sha"
            raise ProductIdentityError(msg, code="action_inconsistent")
    else:
        if not committed:
            msg = f"{action} requires committed=true"
            raise ProductIdentityError(msg, code="action_inconsistent")
        if product_sha == source_sha:
            msg = f"{action} requires product_sha != source_sha"
            raise ProductIdentityError(msg, code="action_inconsistent")

    # Canonical byte comparison: the original bytes must equal the canonical
    # re-serialization exactly. A payload smuggling unencodable text (for
    # example surrogate escapes) fails closed here rather than crashing.
    try:
        canonical = canonical_json_bytes(obj)
    except (UnicodeEncodeError, RecursionError) as exc:
        msg = "identity payload bytes are not canonical"
        raise ProductIdentityError(msg, code="noncanonical_bytes") from exc
    if canonical != data:
        msg = "identity payload bytes are not canonical"
        raise ProductIdentityError(msg, code="noncanonical_bytes")

    return ProductIdentity(
        action=action,
        candidate_digest=candidate_digest,
        committed=committed,
        product_sha=product_sha,
        published=published,
        repository=repository,
        schema_version=schema_version,
        source_sha=source_sha,
        subjects=tuple(subjects),
        workflow_run_attempt=run_attempt,
        workflow_run_id=run_id,
    )


def build_identity_document(
    *,
    action: str,
    candidate_digest: str,
    committed: bool,
    product_sha: str,
    repository: str,
    source_sha: str,
    workflow_run_attempt: int,
    workflow_run_id: int,
    origin_main: str | None,
    object_format: str,
) -> bytes:
    """Build the canonical ProductIdentity document.

    ``published`` is true only when the caller passes a freshly fetched
    ``origin_main`` OID exactly equal to ``product_sha``; a producer with
    unresolved or failed publication never writes an authorizing artifact.
    ``object_format`` is required so writers cannot emit non-OID SHAs.
    The result is self-validated so a writer can never emit a payload the
    consumer would reject.
    """
    if action not in ALLOWED_ACTIONS:
        msg = f"action must be one of {ALLOWED_ACTIONS}"
        raise ProductIdentityError(msg, code="invalid_action")
    if repository != REPOSITORY:
        msg = f"repository must be {REPOSITORY!r}"
        raise ProductIdentityError(msg, code="invalid_value")
    product_sha = validate_oid_text(product_sha, object_format)
    source_sha = validate_oid_text(source_sha, object_format)
    if origin_main is not None:
        origin_main = validate_oid_text(origin_main, object_format)
    published = origin_main is not None and origin_main == product_sha
    payload = {
        "action": action,
        "candidate_digest": candidate_digest,
        "committed": committed,
        "product_sha": product_sha,
        "published": published,
        "repository": repository,
        "schema_version": SCHEMA_VERSION,
        "source_sha": source_sha,
        "subjects": list(PRODUCT_SUBJECTS),
        "workflow_run_attempt": workflow_run_attempt,
        "workflow_run_id": workflow_run_id,
    }
    data = canonical_json_bytes(payload)
    rest = RestMetadata(
        run_id=workflow_run_id,
        run_attempt=workflow_run_attempt,
        repository=repository,
        action=action,
    )
    validate_identity_document(data, rest, object_format=object_format)
    return data


# --- Candidate digest ---------------------------------------------------------


def frame_subject(rel_path: str, data: bytes) -> bytes:
    """Frame one subject: UTF-8 path, NUL, 8-byte LE byte length, raw bytes."""
    return rel_path.encode("utf-8") + b"\x00" + len(data).to_bytes(8, "little") + data


def candidate_digest(subjects: Mapping[str, bytes] | None = None) -> str:
    """Compute the ordered candidate digest over the seven fixed subjects.

    Each subject is framed as UTF-8 relative path + one NUL byte + unsigned
    eight-byte little-endian byte length + raw bytes, concatenated in the
    fixed subject order, then SHA-256, prefixed with ``sha256:``.
    """
    mapping = subjects or {}
    hasher = hashlib.sha256()
    for rel_path in PRODUCT_SUBJECTS:
        hasher.update(frame_subject(rel_path, mapping.get(rel_path, b"")))
    return f"sha256:{hasher.hexdigest()}"


# --- Git plumbing (argv lists only; never shell=True) -------------------------


def _sanitize_stderr(stderr: str) -> str:
    """Reduce git stderr to one capped, URL-redacted line for error messages."""
    stripped = stderr.strip()
    if not stripped:
        return ""
    first = stripped.splitlines()[0]
    return _URL_RE.sub("[redacted-url]", first)[:200]


def run_git(
    repo: Path,
    args: Sequence[str],
    *,
    check: bool = True,
    timeout: float = GIT_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    """Run a git command via an argv list in ``repo`` (never shell=True)."""
    if not args:
        msg = "git command requires at least one argument"
        raise ProductIdentityError(msg, code="git_invocation")
    argv = ["git", *args]
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        result = subprocess.run(
            argv,
            cwd=str(repo),
            capture_output=True,
            text=True,
            check=False,
            env=env,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        msg = f"git command timed out: {args[0]}"
        raise ProductIdentityError(msg, code="git_timeout") from exc
    except FileNotFoundError as exc:
        msg = "git executable not found"
        raise ProductIdentityError(msg, code="git_missing") from exc
    if check and result.returncode != 0:
        stderr = _sanitize_stderr(result.stderr or "")
        msg = f"git {' '.join(args)} failed ({result.returncode}): {stderr}"
        raise ProductIdentityError(msg, code="git_error")
    return result


def _git_output_bytes(repo: Path, args: Sequence[str]) -> bytes:
    argv = ["git", *args]
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        result = subprocess.run(
            argv,
            cwd=str(repo),
            capture_output=True,
            check=False,
            env=env,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        msg = f"git command timed out: {args[0]}"
        raise ProductIdentityError(msg, code="git_timeout") from exc
    if result.returncode != 0:
        stderr = _sanitize_stderr((result.stderr or b"").decode("utf-8", errors="replace"))
        msg = f"git {' '.join(args)} failed ({result.returncode}): {stderr}"
        raise ProductIdentityError(msg, code="git_error")
    return result.stdout


def git_object_format(repo: Path) -> str:
    """Return the repository storage format (``sha1`` or ``sha256``)."""
    result = run_git(repo, ["rev-parse", "--show-object-format=storage"])
    fmt = (result.stdout or "").strip()
    if fmt not in _OID_LENGTHS:
        msg = f"unsupported git object format: {fmt!r}"
        raise ProductIdentityError(msg, code="unsupported_object_format")
    return fmt


def require_commit(repo: Path, oid: str, *, object_format: str | None = None) -> str:
    """Require ``oid`` to name a commit object directly.

    Grammar is validated first (so no OID-shaped injection string reaches a
    subprocess), then canonical resolution must equal the supplied OID
    byte-for-byte, and ``git cat-file -t`` must print ``commit`` directly —
    tag/tree/blob objects are rejected even when they would peel to a commit.
    """
    fmt = object_format or git_object_format(repo)
    validate_oid_text(oid, fmt)
    resolved = run_git(repo, ["rev-parse", oid])
    if resolved.stdout.strip() != oid:
        msg = f"canonical resolution of {oid!r} is not byte-identical"
        raise ProductIdentityError(msg, code="oid_not_canonical")
    kind = run_git(repo, ["cat-file", "-t", oid]).stdout.strip()
    if kind != "commit":
        msg = f"object {oid!r} is a {kind}, not a commit"
        raise ProductIdentityError(msg, code="not_a_commit")
    return oid


def _require_valid_oids(repo: Path, *oids: str) -> None:
    """Grammar-check every OID against the repository's storage format."""
    if not oids:
        return
    fmt = git_object_format(repo)
    for oid in oids:
        validate_oid_text(oid, fmt)


def require_sufficient_history(repo: Path, *oids: str) -> None:
    """Fail closed when needed commits sit behind a shallow-history boundary.

    A commit listed in ``.git/shallow`` (or whose parents cannot be resolved)
    means the depth-limited checkout never has the ancestry required for
    relation or diff validation.
    """
    _require_valid_oids(repo, *oids)
    shallow_path = repo / ".git" / "shallow"
    if shallow_path.is_file() and oids:
        boundaries = {
            line.strip()
            for line in shallow_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
        for oid in oids:
            if oid in boundaries:
                msg = f"{INSUFFICIENT_HISTORY_MESSAGE}: {oid} is a shallow boundary"
                raise ProductIdentityError(msg, code="insufficient_history")
    for oid in oids:
        result = run_git(repo, ["rev-list", "--parents", "-n", "1", oid], check=False)
        if result.returncode != 0:
            msg = f"{INSUFFICIENT_HISTORY_MESSAGE}: cannot resolve parents of {oid}"
            raise ProductIdentityError(msg, code="insufficient_history")


def parent_oids(repo: Path, oid: str) -> list[str]:
    """Return the full parent OID list of a commit (requires full history)."""
    _require_valid_oids(repo, oid)
    result = run_git(repo, ["rev-list", "--parents", "-n", "1", oid], check=False)
    if result.returncode != 0:
        msg = f"{INSUFFICIENT_HISTORY_MESSAGE}: cannot resolve parents of {oid}"
        raise ProductIdentityError(msg, code="insufficient_history")
    return result.stdout.split()[1:]


def _parse_ls_tree(raw: bytes) -> list[TreeEntry]:
    entries: list[TreeEntry] = []
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            meta, path_bytes = record.split(b"\t", 1)
            mode, kind, oid = meta.decode("ascii").split(" ")
            entries.append(TreeEntry(mode, kind, oid, path_bytes.decode("utf-8")))
        except (ValueError, UnicodeDecodeError) as exc:
            msg = f"malformed ls-tree record: {record!r}"
            raise ProductIdentityError(msg, code="git_parse") from exc
    return entries


def product_tree_entries(repo: Path, commit_oid: str) -> dict[str, TreeEntry]:
    """Read and structurally validate the seven product tree entries.

    ``catalog.json`` must be a ``100644`` blob; ``feeds`` must be a ``040000``
    tree containing exactly the six fixed basenames, each a ``100644`` blob.
    Missing leaves, extras (including case variants and nested trees), and
    unauthorized modes/types (executable, symlink ``120000``, gitlink
    ``160000``) fail closed.
    """
    _require_valid_oids(repo, commit_oid)
    root = _parse_ls_tree(
        _git_output_bytes(repo, ["ls-tree", "-z", commit_oid, "--", "catalog.json"])
    )
    if len(root) != 1 or root[0].path != "catalog.json":
        msg = "catalog.json is missing from the commit tree"
        raise ProductIdentityError(msg, code="product_tree_invalid")
    if root[0].mode != "100644" or root[0].kind != "blob":
        msg = f"catalog.json must be a 100644 blob, got {root[0].mode} {root[0].kind}"
        raise ProductIdentityError(msg, code="product_tree_invalid")

    feeds_dir = _parse_ls_tree(
        _git_output_bytes(repo, ["ls-tree", "-z", commit_oid, "--", "feeds"])
    )
    if len(feeds_dir) != 1 or feeds_dir[0].path != "feeds":
        msg = "feeds/ is missing from the commit tree"
        raise ProductIdentityError(msg, code="product_tree_invalid")
    if feeds_dir[0].mode != "040000" or feeds_dir[0].kind != "tree":
        msg = f"feeds must be a 040000 tree, got {feeds_dir[0].mode} {feeds_dir[0].kind}"
        raise ProductIdentityError(msg, code="product_tree_invalid")

    children = _parse_ls_tree(
        _git_output_bytes(repo, ["ls-tree", "-z", commit_oid, "--", "feeds/"])
    )
    names = {entry.path.split("/", 1)[1]: entry for entry in children}
    if set(names) != FEED_BASENAMES:
        msg = f"feeds/ must contain exactly the six fixed basenames, got {sorted(names)}"
        raise ProductIdentityError(msg, code="product_tree_invalid")
    for name, entry in names.items():
        if entry.mode != "100644" or entry.kind != "blob":
            msg = f"feeds/{name} must be a 100644 blob, got {entry.mode} {entry.kind}"
            raise ProductIdentityError(msg, code="product_tree_invalid")

    entries: dict[str, TreeEntry] = {"catalog.json": root[0]}
    for name, entry in names.items():
        entries[f"feeds/{name}"] = entry
    return entries


def read_product_blobs(repo: Path, commit_oid: str) -> dict[str, bytes]:
    """Read the seven product blob byte strings from git blob objects.

    Digests are always recomputed from validated git blobs, never from
    workspace checkout files.
    """
    entries = product_tree_entries(repo, commit_oid)
    blobs: dict[str, bytes] = {}
    for rel_path in PRODUCT_SUBJECTS:
        entry = entries[rel_path]
        blobs[rel_path] = _git_output_bytes(repo, ["cat-file", "blob", entry.oid])
    return blobs


def digest_commit_product(repo: Path, commit_oid: str) -> str:
    """Recompute the candidate digest from a commit's validated git blobs."""
    return candidate_digest(read_product_blobs(repo, commit_oid))


def diff_entries(repo: Path, source_oid: str, product_oid: str) -> list[DiffEntry]:
    """Return raw ``git diff`` records between two commits (no renames)."""
    _require_valid_oids(repo, source_oid, product_oid)
    raw = _git_output_bytes(
        repo,
        [
            "diff",
            "--raw",
            "--no-renames",
            "--no-abbrev",
            "-z",
            source_oid,
            product_oid,
        ],
    )
    entries: list[DiffEntry] = []
    records = raw.split(b"\0")
    idx = 0
    while idx < len(records):
        record = records[idx]
        if not record:
            idx += 1
            continue
        if not record.startswith(b":"):
            msg = f"malformed raw diff record: {record!r}"
            raise ProductIdentityError(msg, code="git_parse")
        meta = record[1:].decode("ascii")
        try:
            path = records[idx + 1].decode("utf-8") if idx + 1 < len(records) else ""
        except UnicodeDecodeError as exc:
            msg = "changed path bytes are not strict UTF-8; failing closed as non-product"
            raise ProductIdentityError(msg, code="non_product_path") from exc
        try:
            old_mode, new_mode, old_oid, new_oid, status = meta.split(" ")
        except ValueError as exc:
            msg = f"malformed raw diff record: {record!r}"
            raise ProductIdentityError(msg, code="git_parse") from exc
        entries.append(DiffEntry(status, path, old_mode, new_mode, old_oid, new_oid))
        idx += 2
    return entries


def _validate_diff_discipline(entries: Sequence[DiffEntry]) -> set[str]:
    """Validate per-path mode/type discipline; return the changed path set."""
    paths: set[str] = set()
    for entry in entries:
        if entry.status == "T":
            msg = f"path type changed: {entry.path}"
            raise ProductIdentityError(msg, code="product_path_type_changed")
        if entry.path not in PRODUCT_SUBJECTS:
            msg = f"non-product path changed: {entry.path}"
            raise ProductIdentityError(msg, code="non_product_path")
        if entry.status == "D":
            msg = f"product leaf deleted: {entry.path}"
            raise ProductIdentityError(msg, code="product_path_deleted")
        if entry.old_mode != "100644" or entry.new_mode != "100644":
            msg = f"product path mode violation: {entry.path} {entry.old_mode}->{entry.new_mode}"
            raise ProductIdentityError(msg, code="product_mode_violation")
        if entry.old_oid == entry.new_oid:
            msg = f"mode-only change on product path: {entry.path}"
            raise ProductIdentityError(msg, code="product_mode_violation")
        paths.add(entry.path)
    return paths


def validate_action(
    repo: Path,
    *,
    action: str,
    source_oid: str,
    product_oid: str,
    candidate_digest: str,
    object_format: str | None = None,
) -> None:
    """Validate a ProductIdentity action against raw git objects.

    - ``unchanged``: ``product_sha == source_sha``, no changed paths, and the
      source commit digest equals the candidate digest;
    - ``state_changed``: committed direct single-parent child of the source
      with exactly ``catalog.json`` changed;
    - ``updated``: committed direct single-parent child with ``catalog.json``
      plus at least one feed changed and no non-product path.

    Changed-path derivation uses raw ``git diff`` name/mode records; digests
    are recomputed from git blob objects of the validated product tree.
    """
    if action not in ALLOWED_ACTIONS:
        msg = f"action must be one of {ALLOWED_ACTIONS}"
        raise ProductIdentityError(msg, code="invalid_action")
    validate_digest_text(candidate_digest)
    fmt = object_format or git_object_format(repo)
    require_commit(repo, source_oid, object_format=fmt)
    require_commit(repo, product_oid, object_format=fmt)

    if action == "unchanged":
        if source_oid != product_oid:
            msg = "unchanged requires product_sha == source_sha"
            raise ProductIdentityError(msg, code="action_inconsistent")
        diff_entries(repo, source_oid, product_oid)
        if digest_commit_product(repo, source_oid) != candidate_digest:
            msg = "unchanged source commit digest does not equal candidate digest"
            raise ProductIdentityError(msg, code="digest_mismatch")
        return

    require_sufficient_history(repo, product_oid)
    parents = parent_oids(repo, product_oid)
    if parents != [source_oid]:
        msg = (
            f"{action} requires an exact direct single-parent child of the source; "
            f"parents={parents}"
        )
        raise ProductIdentityError(msg, code="not_a_direct_child")
    paths = _validate_diff_discipline(diff_entries(repo, source_oid, product_oid))
    if action == "state_changed":
        if paths != {"catalog.json"}:
            msg = f"state_changed requires exactly catalog.json changed; got {sorted(paths)}"
            raise ProductIdentityError(msg, code="action_paths_invalid")
    else:  # updated
        if "catalog.json" not in paths:
            msg = f"updated requires catalog.json changed; got {sorted(paths)}"
            raise ProductIdentityError(msg, code="action_paths_invalid")
        if not any(path.startswith("feeds/") for path in paths):
            msg = f"updated requires at least one feed changed; got {sorted(paths)}"
            raise ProductIdentityError(msg, code="action_paths_invalid")
    if digest_commit_product(repo, product_oid) != candidate_digest:
        msg = "committed digest does not equal recomputed git-blob digest"
        raise ProductIdentityError(msg, code="digest_mismatch")


def _commit_message(repo: Path, oid: str) -> str:
    _require_valid_oids(repo, oid)
    raw = _git_output_bytes(repo, ["cat-file", "commit", oid])
    try:
        decoded = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        msg = f"commit {oid!r} message is not strict UTF-8"
        raise ProductIdentityError(msg, code="commit_unreadable") from exc
    parts = decoded.split("\n\n", 1)
    return parts[1] if len(parts) == 2 else ""


def commit_trailers(repo: Path, oid: str) -> dict[str, list[str]]:
    """Parse commit-message trailers from the final message paragraph."""
    message = _commit_message(repo, oid)
    paragraphs = [p for p in message.split("\n\n") if p.strip()]
    if not paragraphs:
        return {}
    trailers: dict[str, list[str]] = {}
    for line in paragraphs[-1].splitlines():
        line = line.rstrip("\r")
        name, sep, value = line.partition(":")
        if not sep or not name or " " in name:
            return {}
        trailers.setdefault(name, []).append(value.strip())
    return trailers


def _require_single_trailer(trailers: Mapping[str, list[str]], name: str) -> str:
    values = trailers.get(name, [])
    if len(values) != 1:
        msg = f"commit must carry exactly one {name} trailer"
        raise ProductIdentityError(msg, code="trailer_missing")
    return values[0]


# --- Publication and pre-existing-child recovery ------------------------------


def build_push_argv(product_oid: str) -> list[str]:
    """Exact updater push argv: never force, explicit destination ref."""
    return [
        "git",
        "push",
        "--no-follow-tags",
        "origin",
        f"{product_oid}:refs/heads/main",
    ]


def validate_push_argv(argv: Sequence[str]) -> None:
    """Reject any push argv containing a force flag in any spelling."""
    joined = list(argv)
    for flag in joined:
        if flag.startswith("--force") or flag == "-f" or flag.startswith("+"):
            msg = f"force push flag is forbidden: {flag}"
            raise ProductIdentityError(msg, code="force_push_forbidden")
    if "push" not in joined:
        msg = "not a git push argv"
        raise ProductIdentityError(msg, code="git_invocation")


def classify_publication(
    push_outcome: str,
    fetched_origin_main: str | None,
    source_oid: str,
    product_oid: str,
) -> str:
    """Classify a push attempt from freshly fetched remote state.

    Returns one of ``published``, ``not_published_retryable`` (bounded by
    ``MAX_PUSH_RETRIES``), ``not_adopted``, or ``unresolved``. The observed
    remote OID — never the push exit status alone — decides the outcome.
    """
    if push_outcome not in _PUSH_OUTCOMES:
        msg = f"push_outcome must be one of {sorted(_PUSH_OUTCOMES)}"
        raise ProductIdentityError(msg, code="invalid_push_outcome")
    if fetched_origin_main is None:
        return "unresolved"
    if fetched_origin_main == product_oid:
        return "published"
    if fetched_origin_main == source_oid:
        return "not_published_retryable"
    return "not_adopted"


def validate_preexisting_child(
    repo: Path,
    *,
    child_oid: str,
    source_oid: str,
    action: str,
    candidate_digest: str,
    rest: RestMetadata,
    origin_main: str,
    object_format: str | None = None,
    attempted_commit_oid: str | None = None,
) -> None:
    """Adopt a pre-existing published direct child on a fresh rerun.

    Revalidates action paths, modes, the single-parent relation to the
    source, the recomputed digest, the ``Update-Run-Id`` and
    ``Update-Run-Attempt`` commit-message trailers against the injected REST
    metadata record, and a freshly fetched ``origin_main == child_oid``.
    When this run already created its own proposed commit
    (``attempted_commit_oid``), substituting any other child is forbidden.
    """
    if attempted_commit_oid is not None and child_oid != attempted_commit_oid:
        msg = "same-attempt substitution of another child for the proposed commit"
        raise ProductIdentityError(msg, code="same_attempt_substitution")
    fmt = object_format or git_object_format(repo)
    validate_action(
        repo,
        action=action,
        source_oid=source_oid,
        product_oid=child_oid,
        candidate_digest=candidate_digest,
        object_format=fmt,
    )
    trailers = commit_trailers(repo, child_oid)
    run_id = _require_single_trailer(trailers, UPDATE_RUN_ID_TRAILER)
    run_attempt = _require_single_trailer(trailers, UPDATE_RUN_ATTEMPT_TRAILER)
    for name, actual, expected in (
        (UPDATE_RUN_ID_TRAILER, run_id, rest.run_id),
        (UPDATE_RUN_ATTEMPT_TRAILER, run_attempt, rest.run_attempt),
    ):
        try:
            matches = int(actual) == expected and not actual.startswith(("+", "-"))
        except ValueError as exc:
            msg = f"{name} trailer is not an integer: {actual!r}"
            raise ProductIdentityError(msg, code="trailer_invalid") from exc
        if not matches:
            msg = f"{name} trailer {actual!r} does not match REST metadata {expected}"
            raise ProductIdentityError(msg, code="trailer_mismatch")
    if origin_main != child_oid:
        msg = "origin/main does not equal the pre-existing child"
        raise ProductIdentityError(msg, code="origin_mismatch")


# --- Artifact consumption contract ---------------------------------------------


def expected_artifact_name(run_id: int, run_attempt: int) -> str:
    """Producer artifact name template: ``product-identity-<run_id>-<run_attempt>``."""
    run_id = _require_positive_int(run_id, "run_id")
    run_attempt = _require_positive_int(run_attempt, "run_attempt")
    return f"product-identity-{run_id}-{run_attempt}"


def validate_archive_digest(digest: object) -> str:
    """Validate the artifact archive digest field (never a payload hash)."""
    return validate_digest_text(digest)


def validate_artifact_id(value: object) -> int:
    """Require a positive integer numeric artifact ID."""
    return _require_positive_int(value, "artifact_id")


def select_identity_artifact(
    records: Sequence[ArtifactRecord],
    *,
    run_id: int,
    run_attempt: int,
) -> ArtifactRecord:
    """Select the unique non-expired expected artifact from a run-scoped listing."""
    name = expected_artifact_name(run_id, run_attempt)
    matches = [record for record in records if record.name == name]
    active = [record for record in matches if not record.expired]
    if len(active) != 1:
        msg = (
            f"expected exactly one non-expired {name!r} artifact; "
            f"found {len(active)} non-expired of {len(matches)} matching"
        )
        raise ProductIdentityError(msg, code="artifact_selection")
    record = active[0]
    validate_artifact_id(record.artifact_id)
    validate_archive_digest(record.archive_digest)
    return record


def validate_download_directory(directory: Path) -> None:
    """Require a fresh, empty, real (non-symlink) download directory."""
    try:
        info = os.lstat(directory)
    except OSError as exc:
        msg = "download directory must exist"
        raise ProductIdentityError(msg, code="download_directory_invalid") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        msg = "download directory must be a real directory"
        raise ProductIdentityError(msg, code="download_directory_invalid")
    if os.listdir(directory):
        msg = "download directory must be empty"
        raise ProductIdentityError(msg, code="download_directory_invalid")


def require_published(identity: ProductIdentity) -> None:
    """Consumers require literal ``published: true`` on authorizing routes."""
    if identity.published is not True:
        msg = "consumer route requires a literal published:true identity"
        raise ProductIdentityError(msg, code="not_published")


def validate_artifact_file(path: Path, *, max_bytes: int = MAX_ARTIFACT_BYTES) -> bytes:
    """Read one regular non-symlink artifact file under the size ceiling.

    Opens with ``O_NOFOLLOW`` and requires ``fstat`` identity to match the
    validated inode. Directories, special files, and oversized payloads are
    classified violations. This is the path-based counterpart to the raw-bytes
    size check in ``validate_identity_document``.
    """
    try:
        info = os.lstat(path)
    except OSError as exc:
        msg = f"artifact file is not accessible: {path}"
        raise ProductIdentityError(msg, code="artifact_file_invalid") from exc
    if stat.S_ISLNK(info.st_mode):
        msg = f"artifact file must not be a symlink: {path}"
        raise ProductIdentityError(msg, code="symlink_forbidden")
    if not stat.S_ISREG(info.st_mode):
        msg = f"artifact must be a regular file: {path}"
        raise ProductIdentityError(msg, code="artifact_not_file")
    if info.st_size > max_bytes:
        msg = f"artifact file exceeds {max_bytes} bytes"
        raise ProductIdentityError(msg, code="artifact_too_large")
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, os.O_RDONLY | nofollow)
    except OSError as exc:
        msg = f"failed to open artifact without following links: {exc}"
        raise ProductIdentityError(msg, code="symlink_forbidden") from exc
    try:
        fd_info = os.fstat(fd)
        if not stat.S_ISREG(fd_info.st_mode):
            msg = f"artifact is not a regular file after open: {path}"
            raise ProductIdentityError(msg, code="artifact_not_file")
        if fd_info.st_dev != info.st_dev or fd_info.st_ino != info.st_ino:
            msg = f"TOCTOU detected: artifact changed identity after validation: {path}"
            raise ProductIdentityError(msg, code="toctou_violation")
        if fd_info.st_size > max_bytes:
            msg = f"artifact file exceeds {max_bytes} bytes"
            raise ProductIdentityError(msg, code="artifact_too_large")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, 1 << 16)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                msg = f"artifact file exceeds {max_bytes} bytes"
                raise ProductIdentityError(msg, code="artifact_too_large")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def validate_identity_file(
    path: Path,
    rest: RestMetadata,
    *,
    object_format: str | None = None,
) -> ProductIdentity:
    """Validate a ProductIdentity artifact from a path (not raw bytes)."""
    return validate_identity_document(
        validate_artifact_file(path),
        rest,
        object_format=object_format,
    )


# --- CI authorization (schema v1, §2.4) --------------------------------------


@dataclass(frozen=True)
class CiAuthorization:
    """Validated canonical CI authorization record."""

    ci_event: str
    ci_head_sha: str
    ci_run_attempt: int
    ci_run_id: int
    ci_workflow_id: int
    ci_workflow_path: str
    ci_workflow_sha: str
    gate_result: str
    product_digest: str
    product_identity_artifact_digest: str | None
    product_identity_artifact_id: int | None
    repository: str
    route: str
    schema_version: int
    selected_product_sha: str
    update_action: str | None
    update_run_attempt: int | None
    update_run_id: int | None
    update_source_sha: str | None

    def to_dict(self) -> dict[str, object]:
        """Serialize to the exact 19-key set (nulls remain JSON null)."""
        return {
            "ci_event": self.ci_event,
            "ci_head_sha": self.ci_head_sha,
            "ci_run_attempt": self.ci_run_attempt,
            "ci_run_id": self.ci_run_id,
            "ci_workflow_id": self.ci_workflow_id,
            "ci_workflow_path": self.ci_workflow_path,
            "ci_workflow_sha": self.ci_workflow_sha,
            "gate_result": self.gate_result,
            "product_digest": self.product_digest,
            "product_identity_artifact_digest": self.product_identity_artifact_digest,
            "product_identity_artifact_id": self.product_identity_artifact_id,
            "repository": self.repository,
            "route": self.route,
            "schema_version": self.schema_version,
            "selected_product_sha": self.selected_product_sha,
            "update_action": self.update_action,
            "update_run_attempt": self.update_run_attempt,
            "update_run_id": self.update_run_id,
            "update_source_sha": self.update_source_sha,
        }

    def to_bytes(self) -> bytes:
        """Canonical JSON serialization used by CI producers."""
        return canonical_json_bytes(self.to_dict())


def expected_ci_authorization_artifact_name(
    ci_run_id: int,
    ci_run_attempt: int,
    route: str,
    selected_sha: str,
) -> str:
    """CI artifact name: ``ci-authorization-<id>-<attempt>-<route>-<sha>``."""
    ci_run_id = _require_positive_int(ci_run_id, "ci_run_id")
    ci_run_attempt = _require_positive_int(ci_run_attempt, "ci_run_attempt")
    if route not in ALLOWED_CI_ROUTES:
        msg = f"route must be one of {ALLOWED_CI_ROUTES}"
        raise ProductIdentityError(msg, code="invalid_ci_route")
    if not isinstance(selected_sha, str) or not _HEX_RE.fullmatch(selected_sha):
        msg = "selected_sha must be a lowercase hex git object id"
        raise ProductIdentityError(msg, code="invalid_oid")
    return f"ci-authorization-{ci_run_id}-{ci_run_attempt}-{route}-{selected_sha}"


def expected_ci_noop_artifact_name(run_id: int, run_attempt: int) -> str:
    """Non-authorizing unchanged-continuation evidence artifact name."""
    run_id = _require_positive_int(run_id, "run_id")
    run_attempt = _require_positive_int(run_attempt, "run_attempt")
    return f"ci-noop-{run_id}-{run_attempt}.json"


def select_ci_authorization_artifact(
    records: Sequence[ArtifactRecord],
    *,
    ci_run_id: int,
    ci_run_attempt: int,
    route: str,
    selected_sha: str,
) -> ArtifactRecord:
    """Select the unique non-expired CI authorization artifact for a run."""
    name = expected_ci_authorization_artifact_name(ci_run_id, ci_run_attempt, route, selected_sha)
    matches = [record for record in records if record.name == name]
    active = [record for record in matches if not record.expired]
    if len(active) != 1:
        msg = (
            f"expected exactly one non-expired {name!r} artifact; "
            f"found {len(active)} non-expired of {len(matches)} matching"
        )
        raise ProductIdentityError(msg, code="artifact_selection")
    record = active[0]
    validate_artifact_id(record.artifact_id)
    validate_archive_digest(record.archive_digest)
    return record


def resolve_active_workflow_id(
    workflows: Sequence[Mapping[str, object]],
    *,
    path: str,
    name: str,
) -> int:
    """Resolve an active workflow ID by exact path and name (never a hardcoded ID)."""
    matches = [
        item
        for item in workflows
        if isinstance(item, Mapping) and item.get("path") == path and item.get("state") == "active"
    ]
    if len(matches) != 1:
        msg = f"expected exactly one active workflow at {path!r}; found {len(matches)}"
        raise ProductIdentityError(msg, code="workflow_resolution")
    item = matches[0]
    if item.get("name") != name:
        msg = f"workflow at {path!r} has name {item.get('name')!r}, expected {name!r}"
        raise ProductIdentityError(msg, code="workflow_resolution")
    return validate_artifact_id(item.get("id"))


def _require_nullable_positive_int(value: object, name: str) -> int | None:
    if value is None:
        return None
    return _require_positive_int(value, name)


def _require_nullable_digest(value: object, name: str) -> str | None:
    if value is None:
        return None
    try:
        return validate_digest_text(value)
    except ProductIdentityError as exc:
        msg = f"{name} is not a valid archive digest"
        raise ProductIdentityError(msg, code=exc.code) from exc


def _require_nullable_oid(value: object, object_format: str) -> str | None:
    if value is None:
        return None
    return validate_oid_text(value, object_format)


def _require_nullable_action(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value not in ALLOWED_ACTIONS:
        msg = f"update_action must be one of {ALLOWED_ACTIONS} or null"
        raise ProductIdentityError(msg, code="invalid_action")
    return value


def _assert_route_nulls(
    *,
    route: str,
    product_identity_artifact_digest: str | None,
    product_identity_artifact_id: int | None,
    update_action: str | None,
    update_run_attempt: int | None,
    update_run_id: int | None,
    update_source_sha: str | None,
) -> None:
    nullable = {
        "product_identity_artifact_digest": product_identity_artifact_digest,
        "product_identity_artifact_id": product_identity_artifact_id,
        "update_action": update_action,
        "update_run_attempt": update_run_attempt,
        "update_run_id": update_run_id,
        "update_source_sha": update_source_sha,
    }
    if route == "push":
        nonempty = [key for key, value in nullable.items() if value is not None]
        if nonempty:
            msg = f"push route requires JSON null for {sorted(nullable)}; set: {nonempty}"
            raise ProductIdentityError(msg, code="ci_route_invariant")
        return
    missing = [key for key, value in nullable.items() if value is None]
    if missing:
        msg = f"update_continuation requires non-null {sorted(nullable)}; missing: {missing}"
        raise ProductIdentityError(msg, code="ci_route_invariant")


def validate_ci_authorization_document(
    data: bytes,
    *,
    object_format: str,
) -> CiAuthorization:
    """Validate a canonical ``ci-authorization.json`` payload."""
    if len(data) > MAX_ARTIFACT_BYTES:
        msg = f"ci-authorization payload exceeds {MAX_ARTIFACT_BYTES} bytes"
        raise ProductIdentityError(msg, code="artifact_too_large")
    obj = parse_strict_json(data)
    if not isinstance(obj, dict):
        msg = "ci-authorization payload must be a JSON object"
        raise ProductIdentityError(msg, code="json_not_object")
    keys = set(obj)
    missing = sorted(CI_AUTHORIZATION_KEYS - keys)
    extra = sorted(keys - CI_AUTHORIZATION_KEYS)
    if missing or extra:
        msg = f"ci-authorization key set mismatch: missing={missing} extra={extra}"
        raise ProductIdentityError(msg, code="key_set_mismatch")

    schema_version = _require_exact_type(obj["schema_version"], int, "schema_version")
    if schema_version != SCHEMA_VERSION:
        msg = f"schema_version must be {SCHEMA_VERSION}, got {schema_version}"
        raise ProductIdentityError(msg, code="invalid_value")

    route = obj["route"]
    if not isinstance(route, str) or route not in ALLOWED_CI_ROUTES:
        msg = f"route must be one of {ALLOWED_CI_ROUTES}"
        raise ProductIdentityError(msg, code="invalid_ci_route")
    ci_event = obj["ci_event"]
    if not isinstance(ci_event, str) or ci_event not in ALLOWED_CI_EVENTS:
        msg = f"ci_event must be one of {ALLOWED_CI_EVENTS}"
        raise ProductIdentityError(msg, code="invalid_ci_event")
    gate_result = obj["gate_result"]
    if gate_result != GATE_RESULT_PASSED:
        msg = f"gate_result must be {GATE_RESULT_PASSED!r}"
        raise ProductIdentityError(msg, code="invalid_gate_result")
    repository = obj["repository"]
    if repository != REPOSITORY:
        msg = f"repository must be {REPOSITORY!r}"
        raise ProductIdentityError(msg, code="rest_metadata_mismatch")
    workflow_path = obj["ci_workflow_path"]
    if workflow_path != CI_WORKFLOW_PATH:
        msg = f"ci_workflow_path must be {CI_WORKFLOW_PATH!r}"
        raise ProductIdentityError(msg, code="invalid_ci_workflow_path")

    ci_run_id = _require_positive_int(obj["ci_run_id"], "ci_run_id")
    ci_run_attempt = _require_positive_int(obj["ci_run_attempt"], "ci_run_attempt")
    ci_workflow_id = _require_positive_int(obj["ci_workflow_id"], "ci_workflow_id")
    ci_head_sha = validate_oid_text(obj["ci_head_sha"], object_format)
    ci_workflow_sha = validate_oid_text(obj["ci_workflow_sha"], object_format)
    selected_product_sha = validate_oid_text(obj["selected_product_sha"], object_format)
    product_digest = validate_digest_text(obj["product_digest"])

    product_identity_artifact_digest = _require_nullable_digest(
        obj["product_identity_artifact_digest"], "product_identity_artifact_digest"
    )
    product_identity_artifact_id = _require_nullable_positive_int(
        obj["product_identity_artifact_id"], "product_identity_artifact_id"
    )
    update_action = _require_nullable_action(obj["update_action"])
    update_run_attempt = _require_nullable_positive_int(
        obj["update_run_attempt"], "update_run_attempt"
    )
    update_run_id = _require_nullable_positive_int(obj["update_run_id"], "update_run_id")
    update_source_sha = _require_nullable_oid(obj["update_source_sha"], object_format)
    _assert_route_nulls(
        route=route,
        product_identity_artifact_digest=product_identity_artifact_digest,
        product_identity_artifact_id=product_identity_artifact_id,
        update_action=update_action,
        update_run_attempt=update_run_attempt,
        update_run_id=update_run_id,
        update_source_sha=update_source_sha,
    )

    if route == "push":
        if ci_event != "push":
            msg = "push route requires ci_event==push"
            raise ProductIdentityError(msg, code="ci_route_invariant")
        if ci_head_sha != selected_product_sha:
            msg = "push route requires ci_head_sha==selected_product_sha"
            raise ProductIdentityError(msg, code="ci_route_invariant")
    else:
        if ci_event != "workflow_run":
            msg = "update_continuation requires ci_event==workflow_run"
            raise ProductIdentityError(msg, code="ci_route_invariant")

    try:
        canonical = canonical_json_bytes(obj)
    except (UnicodeEncodeError, RecursionError) as exc:
        msg = "ci-authorization payload bytes are not canonical"
        raise ProductIdentityError(msg, code="noncanonical_bytes") from exc
    if canonical != data:
        msg = "ci-authorization payload bytes are not canonical"
        raise ProductIdentityError(msg, code="noncanonical_bytes")

    return CiAuthorization(
        ci_event=ci_event,
        ci_head_sha=ci_head_sha,
        ci_run_attempt=ci_run_attempt,
        ci_run_id=ci_run_id,
        ci_workflow_id=ci_workflow_id,
        ci_workflow_path=workflow_path,
        ci_workflow_sha=ci_workflow_sha,
        gate_result=gate_result,
        product_digest=product_digest,
        product_identity_artifact_digest=product_identity_artifact_digest,
        product_identity_artifact_id=product_identity_artifact_id,
        repository=repository,
        route=route,
        schema_version=schema_version,
        selected_product_sha=selected_product_sha,
        update_action=update_action,
        update_run_attempt=update_run_attempt,
        update_run_id=update_run_id,
        update_source_sha=update_source_sha,
    )


def validate_ci_authorization_file(path: Path, *, object_format: str) -> CiAuthorization:
    """Validate a CI authorization artifact from a path."""
    return validate_ci_authorization_document(
        validate_artifact_file(path),
        object_format=object_format,
    )


def build_ci_authorization_document(
    *,
    ci_event: str,
    ci_head_sha: str,
    ci_run_attempt: int,
    ci_run_id: int,
    ci_workflow_id: int,
    ci_workflow_sha: str,
    product_digest: str,
    product_identity_artifact_digest: str | None,
    product_identity_artifact_id: int | None,
    route: str,
    selected_product_sha: str,
    update_action: str | None,
    update_run_attempt: int | None,
    update_run_id: int | None,
    update_source_sha: str | None,
    object_format: str,
    ci_workflow_path: str = CI_WORKFLOW_PATH,
    repository: str = REPOSITORY,
    gate_result: str = GATE_RESULT_PASSED,
) -> bytes:
    """Build canonical ``ci-authorization.json`` bytes (self-validated)."""
    payload = {
        "ci_event": ci_event,
        "ci_head_sha": ci_head_sha,
        "ci_run_attempt": ci_run_attempt,
        "ci_run_id": ci_run_id,
        "ci_workflow_id": ci_workflow_id,
        "ci_workflow_path": ci_workflow_path,
        "ci_workflow_sha": ci_workflow_sha,
        "gate_result": gate_result,
        "product_digest": product_digest,
        "product_identity_artifact_digest": product_identity_artifact_digest,
        "product_identity_artifact_id": product_identity_artifact_id,
        "repository": repository,
        "route": route,
        "schema_version": SCHEMA_VERSION,
        "selected_product_sha": selected_product_sha,
        "update_action": update_action,
        "update_run_attempt": update_run_attempt,
        "update_run_id": update_run_id,
        "update_source_sha": update_source_sha,
    }
    data = canonical_json_bytes(payload)
    return validate_ci_authorization_document(data, object_format=object_format).to_bytes()


def bind_ci_authorization_to_identity(
    auth: CiAuthorization,
    identity: ProductIdentity,
) -> None:
    """Authorizing update_continuation must match a published ProductIdentity."""
    if auth.route != "update_continuation":
        msg = "bind_ci_authorization_to_identity requires route=update_continuation"
        raise ProductIdentityError(msg, code="ci_route_invariant")
    require_published(identity)
    if auth.selected_product_sha != identity.product_sha:
        msg = "selected_product_sha must equal ProductIdentity.product_sha"
        raise ProductIdentityError(msg, code="ci_identity_mismatch")
    if auth.update_action != identity.action:
        msg = "update_action must equal ProductIdentity.action"
        raise ProductIdentityError(msg, code="ci_identity_mismatch")
    if auth.update_run_id != identity.workflow_run_id:
        msg = "update_run_id must equal ProductIdentity.workflow_run_id"
        raise ProductIdentityError(msg, code="ci_identity_mismatch")
    if auth.update_run_attempt != identity.workflow_run_attempt:
        msg = "update_run_attempt must equal ProductIdentity.workflow_run_attempt"
        raise ProductIdentityError(msg, code="ci_identity_mismatch")
    if auth.update_source_sha != identity.source_sha:
        msg = "update_source_sha must equal ProductIdentity.source_sha"
        raise ProductIdentityError(msg, code="ci_identity_mismatch")
    if auth.product_digest != identity.candidate_digest:
        msg = "product_digest must equal ProductIdentity.candidate_digest"
        raise ProductIdentityError(msg, code="ci_identity_mismatch")


def require_authorizing_identity(identity: ProductIdentity) -> None:
    """Authorizing consumer routes require a published ProductIdentity."""
    require_published(identity)


# --- Exact-OID object acquisition ----------------------------------------------


def build_fetch_argv(oid: str, *, remote: str = "origin") -> list[str]:
    """Read-only exact-OID fetch argv with a grammar-validated remote name."""
    validate_remote_name(remote)
    return ["git", "fetch", "--no-tags", remote, oid]


def ensure_tree_present(repo: Path, oid: str) -> str:
    """Require the full tree of a commit to be present locally."""
    tree_oid = run_git(repo, ["rev-parse", f"{oid}^{{tree}}"], check=False)
    if tree_oid.returncode != 0:
        msg = f"{INSUFFICIENT_HISTORY_MESSAGE}: tree of {oid} is missing"
        raise ProductIdentityError(msg, code="insufficient_history")
    resolved = tree_oid.stdout.strip()
    kind = run_git(repo, ["cat-file", "-t", resolved], check=False)
    if kind.returncode != 0 or kind.stdout.strip() != "tree":
        msg = f"tree object {resolved} of {oid} is not present"
        raise ProductIdentityError(msg, code="insufficient_history")
    return resolved


def fetch_exact_commit(
    repo: Path,
    oid: str,
    *,
    remote: str = "origin",
    object_format: str | None = None,
) -> str:
    """Fetch exactly one commit OID and prove it arrived intact.

    Runs one read-only ``git fetch --no-tags <remote> <oid>``, requires
    ``FETCH_HEAD`` to equal the requested OID byte-for-byte, requires
    ``git cat-file -t <oid>`` to report ``commit``, and requires the commit's
    full tree to be present before any diff or blob validation.
    """
    fmt = object_format or git_object_format(repo)
    validate_oid_text(oid, fmt)
    validate_remote_name(remote)
    argv = build_fetch_argv(oid, remote=remote)
    # ``build_fetch_argv`` returns the full command including the "git" argv[0];
    # ``run_git`` takes only the subcommand arguments.
    result = run_git(repo, argv[1:], check=False)
    if result.returncode != 0:
        stderr = _sanitize_stderr(result.stderr or "")
        msg = f"exact-OID fetch of {oid} failed: {stderr}"
        raise ProductIdentityError(msg, code="fetch_failed")
    fetch_head_path = repo / ".git" / "FETCH_HEAD"
    if not fetch_head_path.is_file():
        msg = "FETCH_HEAD missing after fetch"
        raise ProductIdentityError(msg, code="fetch_unverified")
    first_line = fetch_head_path.read_text(encoding="utf-8").splitlines()[0]
    fetched = first_line.split("\t", 1)[0].strip()
    if fetched != oid:
        msg = f"FETCH_HEAD {fetched!r} does not equal requested OID {oid!r}"
        raise ProductIdentityError(msg, code="fetch_unverified")
    require_commit(repo, oid, object_format=fmt)
    ensure_tree_present(repo, oid)
    return oid


# --- Physical candidate-tree validation ----------------------------------------


def _classify_entry(name: str, mode: int, where: str) -> None:
    if stat.S_ISLNK(mode):
        msg = f"{where}/{name} is a symlink"
        raise ProductIdentityError(msg, code="symlink_forbidden")
    if stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode) or stat.S_ISCHR(mode) or stat.S_ISBLK(mode):
        msg = f"{where}/{name} is a special file"
        raise ProductIdentityError(msg, code="special_file_forbidden")


def validate_candidate_tree(root: Path) -> dict[str, bytes]:
    """Validate a physical candidate product tree and return its leaf bytes.

    The root must be a real directory containing exactly ``catalog.json`` and
    a real ``feeds/`` directory; ``feeds/`` must contain exactly the six fixed
    basenames. Extras (including case variants), nested directories, symlinks
    (including broken links), and special files are classified violations,
    never crashes. Each leaf is opened with ``O_NOFOLLOW`` and its ``fstat``
    identity must match the validated inode (TOCTOU is a violation). The
    complete tree is validated before any byte is copied.
    """
    try:
        root_info = os.lstat(root)
    except OSError as exc:
        msg = f"candidate root is not accessible: {root}"
        raise ProductIdentityError(msg, code="candidate_tree_invalid") from exc
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        msg = "candidate root must be a real directory"
        raise ProductIdentityError(msg, code="candidate_tree_invalid")

    root_names: set[str] = set()
    root_children: dict[str, os.stat_result] = {}
    try:
        with os.scandir(root) as scan:
            for entry in scan:
                info = entry.stat(follow_symlinks=False)
                _classify_entry(entry.name, info.st_mode, str(root))
                root_names.add(entry.name)
                root_children[entry.name] = info
    except OSError as exc:
        msg = f"candidate root is not readable: {root}"
        raise ProductIdentityError(msg, code="candidate_tree_invalid") from exc
    if root_names != {"catalog.json", "feeds"}:
        msg = f"candidate root must contain exactly catalog.json and feeds/: {sorted(root_names)}"
        raise ProductIdentityError(msg, code="candidate_tree_invalid")
    feeds_info = root_children["feeds"]
    catalog_info = root_children["catalog.json"]
    if not stat.S_ISDIR(feeds_info.st_mode):
        msg = "feeds must be a real directory"
        raise ProductIdentityError(msg, code="candidate_tree_invalid")
    if not stat.S_ISREG(catalog_info.st_mode):
        msg = "catalog.json must be a regular file"
        raise ProductIdentityError(msg, code="candidate_tree_invalid")

    feeds_path = root / "feeds"
    feeds_children: dict[str, os.stat_result] = {}
    try:
        with os.scandir(feeds_path) as scan:
            for entry in scan:
                info = entry.stat(follow_symlinks=False)
                _classify_entry(entry.name, info.st_mode, "feeds")
                if stat.S_ISDIR(info.st_mode):
                    msg = f"feeds/{entry.name} is a nested directory"
                    raise ProductIdentityError(msg, code="candidate_tree_invalid")
                feeds_children[entry.name] = info
    except ProductIdentityError:
        raise
    except OSError as exc:
        msg = f"feeds directory is not readable: {feeds_path}"
        raise ProductIdentityError(msg, code="candidate_tree_invalid") from exc
    if set(feeds_children) != FEED_BASENAMES:
        msg = f"feeds must contain exactly the six fixed basenames: {sorted(feeds_children)}"
        raise ProductIdentityError(msg, code="candidate_tree_invalid")

    # Complete structural validation finished; now open and read leaves from
    # the validated fds only. The root and feeds directory identities are
    # pinned with O_NOFOLLOW|O_DIRECTORY descriptors so a path swap between
    # the scans and the leaf opens is detected, not silently followed.
    blobs: dict[str, bytes] = {}
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    dirflags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | nofollow
    root_fd = -1
    feeds_fd = -1
    try:
        try:
            root_fd = os.open(root, dirflags)
        except OSError as exc:
            msg = f"failed to open candidate root without following links: {exc}"
            raise ProductIdentityError(msg, code="candidate_tree_invalid") from exc
        try:
            feeds_fd = os.open("feeds", dirflags, dir_fd=root_fd)
        except OSError as exc:
            msg = f"failed to open feeds without following links: {exc}"
            raise ProductIdentityError(msg, code="symlink_forbidden") from exc
        feeds_fd_info = os.fstat(feeds_fd)
        if feeds_fd_info.st_dev != feeds_info.st_dev or feeds_fd_info.st_ino != feeds_info.st_ino:
            msg = "TOCTOU detected: feeds changed identity after validation"
            raise ProductIdentityError(msg, code="toctou_violation")
        for rel_path in PRODUCT_SUBJECTS:
            if "/" in rel_path:
                leaf_name = rel_path.split("/", 1)[1]
                info = feeds_children[leaf_name]
                open_dir = feeds_fd
            else:
                leaf_name = rel_path
                info = catalog_info
                open_dir = root_fd
            try:
                fd = os.open(leaf_name, os.O_RDONLY | nofollow, dir_fd=open_dir)
            except OSError as exc:
                msg = f"failed to open {rel_path} without following links: {exc}"
                raise ProductIdentityError(msg, code="symlink_forbidden") from exc
            try:
                fd_info = os.fstat(fd)
                if not stat.S_ISREG(fd_info.st_mode):
                    msg = f"{rel_path} is not a regular file after open"
                    raise ProductIdentityError(msg, code="candidate_tree_invalid")
                if fd_info.st_dev != info.st_dev or fd_info.st_ino != info.st_ino:
                    msg = f"TOCTOU detected: {rel_path} changed identity after validation"
                    raise ProductIdentityError(msg, code="toctou_violation")
                chunks: list[bytes] = []
                while True:
                    chunk = os.read(fd, 1 << 20)
                    if not chunk:
                        break
                    chunks.append(chunk)
            finally:
                os.close(fd)
            blobs[rel_path] = b"".join(chunks)
    finally:
        if feeds_fd >= 0:
            os.close(feeds_fd)
        if root_fd >= 0:
            os.close(root_fd)
    return blobs


def candidate_digest_from_tree(root: Path) -> str:
    """Validate a physical candidate tree and compute its candidate digest."""
    return candidate_digest(validate_candidate_tree(root))


def _cli_option(args: Sequence[str], name: str) -> str | None:
    prefix = f"--{name}="
    for arg in args:
        if arg.startswith(prefix):
            return arg[len(prefix) :]
    return None


def main(argv: Sequence[str] | None = None) -> int:
    """Thin CLI: ``constants`` and ``validate`` for trusted workflow steps."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "constants":
        print(f"schema_version={SCHEMA_VERSION}")
        print(f"repository={REPOSITORY}")
        print(f"max_artifact_bytes={MAX_ARTIFACT_BYTES}")
        print(f"max_push_retries={MAX_PUSH_RETRIES}")
        print(f"subjects={','.join(PRODUCT_SUBJECTS)}")
        print(f"empty_digest={candidate_digest()}")
        print(f"ci_workflow_path={CI_WORKFLOW_PATH}")
        print(f"update_workflow_path={UPDATE_WORKFLOW_PATH}")
        return 0
    if len(args) >= 3 and args[0] == "validate":
        kind = args[1]
        path = Path(args[2])
        object_format = _cli_option(args[3:], "object-format") or "sha1"
        try:
            if kind == "identity":
                run_id = _require_positive_int(
                    int(_cli_option(args[3:], "run-id") or "0"), "run_id"
                )
                run_attempt = _require_positive_int(
                    int(_cli_option(args[3:], "run-attempt") or "0"), "run_attempt"
                )
                action = _cli_option(args[3:], "action") or ""
                rest = RestMetadata(run_id, run_attempt, REPOSITORY, action)
                identity = validate_identity_file(path, rest, object_format=object_format)
                print(f"action={identity.action}")
                print(f"product_sha={identity.product_sha}")
                print(f"published={str(identity.published).lower()}")
                return 0
            if kind == "ci-authorization":
                auth = validate_ci_authorization_file(path, object_format=object_format)
                print(f"route={auth.route}")
                print(f"selected_product_sha={auth.selected_product_sha}")
                print(f"gate_result={auth.gate_result}")
                return 0
        except (ProductIdentityError, ValueError, TypeError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"unknown validate kind: {kind}", file=sys.stderr)
        return 2
    print(
        "usage: product_identity.py constants | "
        "validate identity <file> --run-id= --run-attempt= --action= "
        "[--object-format=sha1] | validate ci-authorization <file> [--object-format=sha1]",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
