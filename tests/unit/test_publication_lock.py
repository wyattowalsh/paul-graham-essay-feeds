"""Unit tests for locked staged publication."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from paul_graham_essay_feeds.catalog import load_catalog
from paul_graham_essay_feeds.models import Catalog, CatalogEntry, FeedError
from paul_graham_essay_feeds.publication import (
    WriteLock,
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
    assert isinstance(lock, WriteLock)
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
    assert isinstance(a, WriteLock)
    assert a.fd >= 0
    assert a.token
    try:
        with pytest.raises(FeedError, match="Timed out"):
            acquire_write_lock(tmp_path, timeout=0.15)
    finally:
        release_write_lock(a)


def test_double_release_is_safe(tmp_path: Path) -> None:
    lock = acquire_write_lock(tmp_path, timeout=1.0)
    release_write_lock(lock)
    release_write_lock(lock)
    waiter = acquire_write_lock(tmp_path, timeout=1.0)
    try:
        assert isinstance(waiter, WriteLock)
    finally:
        release_write_lock(waiter)


def test_orphaned_lockfile_without_holder_is_acquirable(tmp_path: Path) -> None:
    lock_path = tmp_path / ".cache" / "write.lock"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text("stale-orphaned\n", encoding="utf-8")
    lock = acquire_write_lock(tmp_path, timeout=1.0)
    try:
        assert isinstance(lock, WriteLock)
        assert lock.path == lock_path
    finally:
        release_write_lock(lock)


def test_lock_write_failure_releases_flock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import paul_graham_essay_feeds.publication as pub

    def boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    original = pub._write_lock_fd
    monkeypatch.setattr(pub, "_write_lock_fd", boom)
    with pytest.raises(FeedError, match="Failed to write write lock"):
        acquire_write_lock(tmp_path, timeout=1.0)
    monkeypatch.setattr(pub, "_write_lock_fd", original)
    waiter = acquire_write_lock(tmp_path, timeout=1.0)
    try:
        assert isinstance(waiter, WriteLock)
    finally:
        release_write_lock(waiter)
