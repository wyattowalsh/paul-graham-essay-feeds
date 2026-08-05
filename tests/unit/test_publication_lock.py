"""Unit tests for locked staged publication."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from paul_graham_essay_feeds.catalog import load_catalog
from paul_graham_essay_feeds.models import Catalog, CatalogEntry
from paul_graham_essay_feeds.publication import (
    acquire_write_lock,
    materialize_generation,
    recover_materialize,
    release_write_lock,
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


def test_stage_materialize_and_recover(tmp_path: Path) -> None:
    cat = _catalog()
    blob = b"<rss/>"
    gen = write_staging_generation(
        tmp_path,
        catalog=cat,
        rss=blob,
        atom=blob,
        json_feed=b'{"items":[]}\n',
        simple_rss=blob,
        simple_atom=blob,
        simple_json_feed=b'{"items":[]}\n',
    )
    lock = acquire_write_lock(tmp_path)
    try:
        materialize_generation(tmp_path, gen_id=gen)
    finally:
        release_write_lock(lock)
    assert (tmp_path / "catalog.json").is_file()
    assert (tmp_path / "feeds" / "rss.xml").is_file()
    loaded = load_catalog(tmp_path / "catalog.json")
    assert loaded is not None
    assert loaded.entry_order == cat.entry_order

    # Pointer cleared after success; recover is a no-op.
    assert recover_materialize(tmp_path) is False


def test_lock_exclusive(tmp_path: Path) -> None:
    a = acquire_write_lock(tmp_path, timeout=0.2)
    try:
        import pytest

        from paul_graham_essay_feeds.models import FeedError

        with pytest.raises(FeedError, match="Timed out"):
            acquire_write_lock(tmp_path, timeout=0.15)
    finally:
        release_write_lock(a)
