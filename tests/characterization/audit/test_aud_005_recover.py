"""AUD-005: fail-closed recover (quarantine, never silently delete)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from paul_graham_essay_feeds.models import (
    MATERIALIZE_POINTER_SCHEMA_VERSION,
    Catalog,
    CatalogEntry,
    FeedError,
    MaterializePhase,
    MaterializePointer,
)
from paul_graham_essay_feeds.publication import (
    abandon_recovery,
    recover_materialize,
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


def _pointer_path(root: Path) -> Path:
    path = root / ".cache" / "materialize.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _write_pointer(
    root: Path,
    *,
    gen_id: str,
    phase: str = "materializing",
    schema_version: int = MATERIALIZE_POINTER_SCHEMA_VERSION,
) -> Path:
    path = _pointer_path(root)
    payload = {"schema_version": schema_version, "gen_id": gen_id, "phase": phase}
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return path


def _pointer_not_silently_deleted(root: Path) -> None:
    original = root / ".cache" / "materialize.json"
    quarantine = root / ".cache" / "quarantine"
    quarantined = list(quarantine.glob("*/materialize.json")) if quarantine.is_dir() else []
    assert original.is_file() or quarantined, "pointer was silently deleted"


def test_missing_pointer_is_noop(tmp_path: Path) -> None:
    assert recover_materialize(tmp_path) is False
    assert not (tmp_path / ".cache" / "quarantine").exists()


def test_valid_materializing_pointer_recovers(tmp_path: Path) -> None:
    gen = _stage(tmp_path)
    pointer = MaterializePointer(
        schema_version=MATERIALIZE_POINTER_SCHEMA_VERSION,
        gen_id=gen,
        phase=MaterializePhase.MATERIALIZING,
    )
    _pointer_path(tmp_path).write_text(
        json.dumps(pointer.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8"
    )
    assert recover_materialize(tmp_path) is True
    assert (tmp_path / "catalog.json").is_file()
    assert not (tmp_path / ".cache" / "materialize.json").exists()


def test_valid_complete_pointer_recovers(tmp_path: Path) -> None:
    gen = _stage(tmp_path)
    _write_pointer(tmp_path, gen_id=gen, phase="complete")
    assert recover_materialize(tmp_path) is True
    assert (tmp_path / "feeds" / "rss.xml").is_file()


def test_malformed_json_raises_and_preserves_pointer(tmp_path: Path) -> None:
    path = _pointer_path(tmp_path)
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(FeedError):
        recover_materialize(tmp_path)
    _pointer_not_silently_deleted(tmp_path)
    assert recover_materialize(tmp_path) is False


def test_unknown_schema_version_raises_and_preserves_pointer(tmp_path: Path) -> None:
    gen = _stage(tmp_path)
    _write_pointer(tmp_path, gen_id=gen, schema_version=99)
    with pytest.raises(FeedError):
        recover_materialize(tmp_path)
    _pointer_not_silently_deleted(tmp_path)
    assert recover_materialize(tmp_path) is False


def test_unknown_phase_raises_and_preserves_pointer(tmp_path: Path) -> None:
    gen = _stage(tmp_path)
    _write_pointer(tmp_path, gen_id=gen, phase="abandoned")
    with pytest.raises(FeedError):
        recover_materialize(tmp_path)
    _pointer_not_silently_deleted(tmp_path)
    assert recover_materialize(tmp_path) is False


def test_missing_generation_raises_and_preserves_pointer(tmp_path: Path) -> None:
    _write_pointer(tmp_path, gen_id="0" * 32, phase="materializing")
    with pytest.raises(FeedError, match="Missing staged generation"):
        recover_materialize(tmp_path)
    _pointer_not_silently_deleted(tmp_path)
    assert recover_materialize(tmp_path) is False


def test_digest_failure_raises_and_preserves_pointer(tmp_path: Path) -> None:
    gen = _stage(tmp_path)
    (tmp_path / ".cache" / "generations" / gen / "feeds" / "rss.xml").write_bytes(b"CORRUPT")
    _write_pointer(tmp_path, gen_id=gen, phase="materializing")
    with pytest.raises(FeedError, match="digest mismatch"):
        recover_materialize(tmp_path)
    _pointer_not_silently_deleted(tmp_path)
    assert recover_materialize(tmp_path) is False


def test_unreadable_pointer_raises(tmp_path: Path) -> None:
    path = _pointer_path(tmp_path)
    path.write_bytes(b"\xff\xfe")
    with pytest.raises(FeedError):
        recover_materialize(tmp_path)
    _pointer_not_silently_deleted(tmp_path)
    assert recover_materialize(tmp_path) is False


def test_abandon_recovery_is_safe_without_pointer(tmp_path: Path) -> None:
    abandon_recovery(tmp_path)
    assert recover_materialize(tmp_path) is False


def test_abandon_recovery_fallback_when_quarantine_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import paul_graham_essay_feeds.publication as pub

    gen = _stage(tmp_path)
    _write_pointer(tmp_path, gen_id=gen, phase="materializing")

    def boom(*_args: object, **_kwargs: object) -> Path:
        raise OSError("quarantine unavailable")

    monkeypatch.setattr(pub, "_quarantine_pointer_and_generation", boom)
    abandon_recovery(tmp_path)
    assert not (tmp_path / ".cache" / "materialize.json").exists()
    assert recover_materialize(tmp_path) is False


def test_abandon_recovery_makes_later_recover_a_noop(tmp_path: Path) -> None:
    gen = _stage(tmp_path)
    _write_pointer(tmp_path, gen_id=gen, phase="materializing")
    abandon_recovery(tmp_path)
    assert not (tmp_path / ".cache" / "materialize.json").exists()
    assert recover_materialize(tmp_path) is False
    assert not (tmp_path / "catalog.json").exists()


@pytest.mark.parametrize("gen_id", ["../feeds", "../../feeds", "/tmp/pgf-escape"])
def test_recover_rejects_traversing_gen_id_without_moving_feeds(
    tmp_path: Path, gen_id: str
) -> None:
    feeds = tmp_path / "feeds"
    feeds.mkdir()
    marker = feeds / "keep.txt"
    marker.write_text("safe\n", encoding="utf-8")
    _write_pointer(tmp_path, gen_id=gen_id, phase="materializing")
    with pytest.raises(FeedError):
        recover_materialize(tmp_path)
    _pointer_not_silently_deleted(tmp_path)
    assert marker.is_file()
    assert marker.read_text(encoding="utf-8") == "safe\n"
    assert recover_materialize(tmp_path) is False


@pytest.mark.parametrize("gen_id", ["../feeds", "..", "/tmp/pgf-escape"])
def test_abandon_rejects_traversing_gen_id_without_deleting_cache_root(
    tmp_path: Path, gen_id: str
) -> None:
    cache = tmp_path / ".cache"
    cache.mkdir()
    sentinel = cache / "sidecar.txt"
    sentinel.write_text("keep\n", encoding="utf-8")
    feeds = tmp_path / "feeds"
    feeds.mkdir()
    (feeds / "keep.txt").write_text("safe\n", encoding="utf-8")
    _write_pointer(tmp_path, gen_id=gen_id, phase="materializing")
    abandon_recovery(tmp_path)
    assert sentinel.is_file()
    assert (feeds / "keep.txt").is_file()
    assert recover_materialize(tmp_path) is False
