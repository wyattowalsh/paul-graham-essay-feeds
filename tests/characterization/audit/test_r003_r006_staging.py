"""RV-R-003 / RV-R-006: staging manifest verify + atomic pointer writes."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from paul_graham_essay_feeds.models import Catalog, CatalogEntry, FeedError
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
    materialize_generation(tmp_path, gen_id=gen)
    assert (tmp_path / "catalog.json").is_file()
    assert (tmp_path / "feeds" / "rss.xml").is_file()


def test_pointer_uses_atomic_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pointer path must go through atomic_write_text (not Path.write_text)."""
    import paul_graham_essay_feeds.publication as pub

    calls: list[Path] = []
    original = pub.atomic_write_text

    def spy(path: Path, text: str, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(Path(path))
        return original(path, text, **kwargs)

    monkeypatch.setattr(pub, "atomic_write_text", spy)
    gen = _stage(tmp_path)
    materialize_generation(tmp_path, gen_id=gen)
    assert any(p.name == "materialize.json" for p in calls)
