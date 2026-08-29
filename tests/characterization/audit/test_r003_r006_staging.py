"""RV-R-003 / RV-R-006: staging manifest verify + atomic pointer writes."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from paul_graham_essay_feeds.models import (
    MATERIALIZE_POINTER_SCHEMA_VERSION,
    STAGING_MANIFEST_SCHEMA_VERSION,
    Catalog,
    CatalogEntry,
    FeedError,
    StagingManifest,
)
from paul_graham_essay_feeds.publication import (
    materialize_generation,
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


def _stage(tmp_path: Path) -> str:
    blob = b"<rss/>"
    return write_staging_generation(
        tmp_path,
        catalog=_catalog(),
        rss=blob,
        atom=blob,
        json_feed=b'{"items":[]}\n',
        simple_rss=blob,
        simple_atom=blob,
        simple_json_feed=b'{"items":[]}\n',
    )


def test_corrupt_staged_feed_fails_materialize(tmp_path: Path) -> None:
    gen = _stage(tmp_path)
    staged = tmp_path / ".cache" / "generations" / gen / "feeds" / "rss.xml"
    staged.write_bytes(b"CORRUPTED")
    with pytest.raises(FeedError, match=r"digest mismatch|Staged"):
        materialize_generation(tmp_path, gen_id=gen)
    # Public paths must not appear when integrity fails.
    assert not (tmp_path / "feeds" / "rss.xml").exists()


def test_happy_materialize_writes_public(tmp_path: Path) -> None:
    gen = _stage(tmp_path)
    manifest_path = tmp_path / ".cache" / "generations" / gen / "MANIFEST.json"
    manifest = StagingManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    assert manifest.schema_version == STAGING_MANIFEST_SCHEMA_VERSION
    assert manifest.gen_id == gen
    materialize_generation(tmp_path, gen_id=gen)
    assert (tmp_path / "catalog.json").is_file()
    assert (tmp_path / "feeds" / "rss.xml").is_file()


def test_pointer_uses_atomic_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pointer path must go through atomic_write_text (not Path.write_text)."""
    import paul_graham_essay_feeds.publication as pub

    calls: list[Path] = []
    pointer_texts: list[tuple[Path, str]] = []
    original = pub.atomic_write_text

    def spy(path: Path, text: str, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(Path(path))
        pointer_texts.append((Path(path), text))
        return original(path, text, **kwargs)

    monkeypatch.setattr(pub, "atomic_write_text", spy)
    gen = _stage(tmp_path)
    materialize_generation(tmp_path, gen_id=gen)
    assert any(p.name == "materialize.json" for p in calls)
    pointer_payloads = [
        json.loads(text) for path, text in pointer_texts if path.name == "materialize.json"
    ]
    assert pointer_payloads
    assert all(
        payload["schema_version"] == MATERIALIZE_POINTER_SCHEMA_VERSION
        for payload in pointer_payloads
    )
    assert all(payload["gen_id"] == gen for payload in pointer_payloads)
