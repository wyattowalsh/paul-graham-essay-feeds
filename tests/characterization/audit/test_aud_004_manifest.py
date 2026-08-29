"""AUD-004: exact contained staging MANIFEST (seven artifacts, no escape)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from paul_graham_essay_feeds.models import STAGING_ARTIFACT_RELS, Catalog, CatalogEntry, FeedError
from paul_graham_essay_feeds.publication import (
    verify_staging_manifest,
    write_staging_generation,
)

T0 = datetime(2024, 1, 1, tzinfo=UTC)


def _catalog() -> Catalog:
    e = CatalogEntry(
        stable_id="https://paulgraham.com/a.html",
        url="https://paulgraham.com/a.html",
        title="A",
        position=0,
        first_seen_at=T0,
        last_seen_at=T0,
        observed_updated_at=T0,
        summary="Short summary content for tests.",
    )
    return Catalog(
        schema_version=2,
        material_config_fingerprint="t",
        entry_order=[e.stable_id],
        entries={e.stable_id: e},
    )


def _stage(tmp_path: Path) -> Path:
    blob = b"<rss/>"
    gen = write_staging_generation(
        tmp_path,
        catalog=_catalog(),
        rss=blob,
        atom=blob,
        json_feed=b'{"items":[]}\n',
        simple_rss=blob,
        simple_atom=blob,
        simple_json_feed=b'{"items":[]}\n',
    )
    return tmp_path / ".cache" / "generations" / gen


def _rewrite_manifest(gen_dir: Path, **overrides: object) -> None:
    data = json.loads((gen_dir / "MANIFEST.json").read_text(encoding="utf-8"))
    data.update(overrides)
    (gen_dir / "MANIFEST.json").write_text(
        json.dumps(data, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


def test_happy_manifest_is_exact_seven(tmp_path: Path) -> None:
    gen_dir = _stage(tmp_path)
    verify_staging_manifest(gen_dir)
    data = json.loads((gen_dir / "MANIFEST.json").read_text(encoding="utf-8"))
    assert set(data["files"]) == set(STAGING_ARTIFACT_RELS)
    assert data["schema_version"] == 1
    assert data["gen_id"]


def test_rejects_dotdot_traversal(tmp_path: Path) -> None:
    gen_dir = _stage(tmp_path)
    data = json.loads((gen_dir / "MANIFEST.json").read_text(encoding="utf-8"))
    files = dict(data["files"])
    digest = files.pop("feeds/rss.xml")
    files["feeds/../rss.xml"] = digest
    _rewrite_manifest(gen_dir, files=files)
    with pytest.raises(FeedError):
        verify_staging_manifest(gen_dir)


def test_rejects_absolute_path(tmp_path: Path) -> None:
    gen_dir = _stage(tmp_path)
    data = json.loads((gen_dir / "MANIFEST.json").read_text(encoding="utf-8"))
    files = dict(data["files"])
    digest = files.pop("catalog.json")
    files["/catalog.json"] = digest
    _rewrite_manifest(gen_dir, files=files)
    with pytest.raises(FeedError):
        verify_staging_manifest(gen_dir)


def test_rejects_symlink_escape(tmp_path: Path) -> None:
    gen_dir = _stage(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"secret")
    catalog = gen_dir / "catalog.json"
    catalog.unlink()
    catalog.symlink_to(outside)
    with pytest.raises(FeedError, match=r"escape|symlink"):
        verify_staging_manifest(gen_dir)


def test_rejects_missing_artifact(tmp_path: Path) -> None:
    gen_dir = _stage(tmp_path)
    (gen_dir / "feeds" / "rss.xml").unlink()
    with pytest.raises(FeedError, match=r"missing|missing for MANIFEST"):
        verify_staging_manifest(gen_dir)


def test_rejects_extra_file_on_disk(tmp_path: Path) -> None:
    gen_dir = _stage(tmp_path)
    (gen_dir / "extra.txt").write_text("nope", encoding="utf-8")
    with pytest.raises(FeedError, match="extra"):
        verify_staging_manifest(gen_dir)


def test_rejects_extra_manifest_key(tmp_path: Path) -> None:
    gen_dir = _stage(tmp_path)
    data = json.loads((gen_dir / "MANIFEST.json").read_text(encoding="utf-8"))
    files = dict(data["files"])
    files["feeds/extra.xml"] = files["feeds/rss.xml"]
    _rewrite_manifest(gen_dir, files=files)
    with pytest.raises(FeedError):
        verify_staging_manifest(gen_dir)


def test_rejects_malformed_digest(tmp_path: Path) -> None:
    gen_dir = _stage(tmp_path)
    data = json.loads((gen_dir / "MANIFEST.json").read_text(encoding="utf-8"))
    files = dict(data["files"])
    files["catalog.json"] = "not-a-digest"
    _rewrite_manifest(gen_dir, files=files)
    with pytest.raises(FeedError):
        verify_staging_manifest(gen_dir)


def test_rejects_digest_mismatch(tmp_path: Path) -> None:
    gen_dir = _stage(tmp_path)
    data = json.loads((gen_dir / "MANIFEST.json").read_text(encoding="utf-8"))
    files = dict(data["files"])
    files["catalog.json"] = hashlib.sha256(b"wrong").hexdigest()
    _rewrite_manifest(gen_dir, files=files)
    with pytest.raises(FeedError, match="digest mismatch"):
        verify_staging_manifest(gen_dir)


def test_rejects_missing_gen_id(tmp_path: Path) -> None:
    gen_dir = _stage(tmp_path)
    data = json.loads((gen_dir / "MANIFEST.json").read_text(encoding="utf-8"))
    data.pop("gen_id")
    (gen_dir / "MANIFEST.json").write_text(
        json.dumps(data, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    with pytest.raises(FeedError):
        verify_staging_manifest(gen_dir)


def test_rejects_missing_schema_version(tmp_path: Path) -> None:
    gen_dir = _stage(tmp_path)
    data = json.loads((gen_dir / "MANIFEST.json").read_text(encoding="utf-8"))
    data.pop("schema_version")
    (gen_dir / "MANIFEST.json").write_text(
        json.dumps(data, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    with pytest.raises(FeedError):
        verify_staging_manifest(gen_dir)


def test_rejects_missing_manifest(tmp_path: Path) -> None:
    gen_dir = _stage(tmp_path)
    (gen_dir / "MANIFEST.json").unlink()
    with pytest.raises(FeedError, match="Missing staging MANIFEST"):
        verify_staging_manifest(gen_dir)


def test_rejects_unreadable_manifest(tmp_path: Path) -> None:
    gen_dir = _stage(tmp_path)
    (gen_dir / "MANIFEST.json").write_bytes(b"\xff\xfe")
    with pytest.raises(FeedError, match="Unreadable staging MANIFEST"):
        verify_staging_manifest(gen_dir)


def test_rejects_directory_in_place_of_file(tmp_path: Path) -> None:
    gen_dir = _stage(tmp_path)
    catalog = gen_dir / "catalog.json"
    catalog.unlink()
    catalog.mkdir()
    with pytest.raises(FeedError, match="regular file"):
        verify_staging_manifest(gen_dir)


def test_rejects_unknown_schema_version(tmp_path: Path) -> None:
    gen_dir = _stage(tmp_path)
    _rewrite_manifest(gen_dir, schema_version=99)
    with pytest.raises(FeedError):
        verify_staging_manifest(gen_dir)
