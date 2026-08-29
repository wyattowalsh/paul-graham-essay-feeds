"""AUD-009: GC staged generations (keep ≤2; never delete pointed gen)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from paul_graham_essay_feeds.models import (
    MATERIALIZE_POINTER_SCHEMA_VERSION,
    Catalog,
    CatalogEntry,
)
from paul_graham_essay_feeds.publication import (
    _gc_staged_generations,
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


def _remaining(tmp_path: Path) -> set[str]:
    gen_root = tmp_path / ".cache" / "generations"
    if not gen_root.is_dir():
        return set()
    return {p.name for p in gen_root.iterdir() if p.is_dir()}


def test_repeated_materialize_keeps_at_most_two(tmp_path: Path) -> None:
    ids: list[str] = []
    for _ in range(5):
        gen = _stage(tmp_path)
        materialize_generation(tmp_path, gen_id=gen)
        ids.append(gen)
        remaining = _remaining(tmp_path)
        assert len(remaining) <= 2
        assert gen in remaining
    remaining = _remaining(tmp_path)
    assert remaining == {ids[-1], ids[-2]}
    assert ids[0] not in remaining
    assert not (tmp_path / ".cache" / "materialize.json").exists()


def test_pointed_generation_is_never_deleted(tmp_path: Path) -> None:
    ids = [_stage(tmp_path) for _ in range(4)]
    pointer = tmp_path / ".cache" / "materialize.json"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(
        json.dumps(
            {
                "schema_version": MATERIALIZE_POINTER_SCHEMA_VERSION,
                "gen_id": ids[0],
                "phase": "complete",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _gc_staged_generations(tmp_path, current_gen_id=ids[-1])
    remaining = _remaining(tmp_path)
    assert ids[0] in remaining
    assert ids[-1] in remaining
    assert ids[1] not in remaining


def test_size_cap_drops_unprotected_previous(tmp_path: Path) -> None:
    ids = [_stage(tmp_path) for _ in range(2)]
    _gc_staged_generations(tmp_path, current_gen_id=ids[-1], max_bytes=1)
    remaining = _remaining(tmp_path)
    assert remaining == {ids[-1]}
    assert ids[0] not in remaining
