"""Unit tests for locked staged publication."""

from __future__ import annotations

import json
import os
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pytest

from paul_graham_essay_feeds.catalog import load_catalog
from paul_graham_essay_feeds.models import Catalog, CatalogEntry, FeedError, MaterializePhase
from paul_graham_essay_feeds.publication import (
    WriteLock,
    acquire_write_lock,
    materialize_generation,
    recover_materialize,
    release_write_lock,
    write_staging_generation,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]

T0 = datetime(2024, 1, 1, tzinfo=UTC)


def _catalog(*, schema_version: Literal[1, 2, 3] = 2) -> Catalog:
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
        schema_version=schema_version,
        material_config_fingerprint="t",
        entry_order=[e.stable_id],
        entries={e.stable_id: e},
    )


def _stage_bytes() -> dict[str, bytes]:
    blob = b"<rss/>"
    return {
        "rss": blob,
        "atom": blob,
        "json_feed": b'{"items":[]}\n',
        "simple_rss": blob,
        "simple_atom": blob,
        "simple_json_feed": b'{"items":[]}\n',
    }


def test_stage_materialize_and_recover(tmp_path: Path) -> None:
    cat = _catalog()
    gen = write_staging_generation(
        tmp_path,
        catalog=cat,
        **_stage_bytes(),
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
    assert loaded.last_generation_id == gen

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
    path = lock.path
    inode = path.stat().st_ino
    release_write_lock(lock)
    assert path.is_file()
    assert path.stat().st_ino == inode
    release_write_lock(lock)
    waiter = acquire_write_lock(tmp_path, timeout=1.0)
    try:
        assert isinstance(waiter, WriteLock)
        assert waiter.path.stat().st_ino == inode
    finally:
        release_write_lock(waiter)


def test_release_keeps_stable_lock_inode(tmp_path: Path) -> None:
    lock = acquire_write_lock(tmp_path, timeout=1.0)
    path = lock.path
    inode = os.fstat(lock.fd).st_ino
    try:
        assert path.stat().st_ino == inode
    finally:
        release_write_lock(lock)
    assert path.is_file()
    assert path.stat().st_ino == inode
    waiter = acquire_write_lock(tmp_path, timeout=1.0)
    try:
        assert os.fstat(waiter.fd).st_ino == inode
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["token"] == waiter.token
        assert payload["token"] != lock.token
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


@pytest.mark.parametrize("schema_version", [2, 3])
def test_staging_stamps_last_generation_id(
    tmp_path: Path, schema_version: Literal[2, 3], monkeypatch: pytest.MonkeyPatch
) -> None:
    import paul_graham_essay_feeds.publication as pub

    pointer_ids: list[str] = []
    original_pointer = pub._write_pointer

    def _spy_pointer(path: Path, *, gen_id: str, phase: MaterializePhase) -> None:
        pointer_ids.append(gen_id)
        original_pointer(path, gen_id=gen_id, phase=phase)

    monkeypatch.setattr(pub, "_write_pointer", _spy_pointer)
    cat = _catalog(schema_version=schema_version)
    assert cat.last_generation_id is None
    gen = write_staging_generation(tmp_path, catalog=cat, **_stage_bytes())
    assert cat.last_generation_id is None
    gen_dir = tmp_path / ".cache" / "generations" / gen
    staged = json.loads((gen_dir / "catalog.json").read_text(encoding="utf-8"))
    manifest = json.loads((gen_dir / "MANIFEST.json").read_text(encoding="utf-8"))
    assert staged["last_generation_id"] == gen
    assert staged["schema_version"] == schema_version
    assert manifest["gen_id"] == gen
    materialize_generation(tmp_path, gen_id=gen)
    public_raw = json.loads((tmp_path / "catalog.json").read_text(encoding="utf-8"))
    assert public_raw["last_generation_id"] == gen
    assert public_raw["schema_version"] == schema_version
    public = load_catalog(tmp_path / "catalog.json")
    assert public is not None
    assert public.last_generation_id == gen
    assert pointer_ids
    assert set(pointer_ids) == {gen}


def test_publication_imports_posix_fcntl() -> None:
    import fcntl as posix_fcntl

    import paul_graham_essay_feeds.publication as pub

    assert pub.fcntl is posix_fcntl


def test_pyproject_os_classifiers_are_posix() -> None:
    data = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    classifiers = data["project"]["classifiers"]
    os_cls = [c for c in classifiers if c.startswith("Operating System ::")]
    assert "Operating System :: OS Independent" not in os_cls
    assert "Operating System :: POSIX" in os_cls
    assert "Operating System :: MacOS" in os_cls
