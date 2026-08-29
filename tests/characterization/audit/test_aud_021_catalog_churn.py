"""AUD-021: compact catalog.json diffs (omit position + shared last_seen_at)."""

from __future__ import annotations

import difflib
import json
from datetime import UTC, datetime

import pytest

from paul_graham_essay_feeds.catalog import (
    CATALOG_SCHEMA_VERSION,
    catalog_material_summary,
    catalog_to_json,
    migrate_catalog,
    reconcile_discovery,
)
from paul_graham_essay_feeds.models import Catalog, CatalogEntry, DiscoveryItem, ResourceState

T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
T1 = datetime(2024, 6, 15, 9, 30, 0, tzinfo=UTC)

pytestmark = pytest.mark.characterization


def _item(*, slug: str) -> DiscoveryItem:
    url = f"https://paulgraham.com/{slug}.html"
    return DiscoveryItem(
        position=1,
        title=slug.upper(),
        url=url,
        stable_id=url,
        is_permalink=True,
    )


def _with_shared_index(catalog: Catalog, *, observed: datetime) -> Catalog:
    return catalog.model_copy(
        update={
            "index": ResourceState(
                last_checked_at=observed,
                last_attempted_at=observed,
                last_response_at=observed,
                last_success_at=observed,
            )
        }
    )


def _json_edits(before: str, after: str) -> list[str]:
    lines = list(difflib.unified_diff(before.splitlines(), after.splitlines(), n=0, lineterm=""))
    return [
        line
        for line in lines
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]


def test_aud_021_insert_one_does_not_rewrite_every_position() -> None:
    items = [_item(slug=f"e{i}") for i in range(5)]
    prior, _ = reconcile_discovery(None, items, now=T0)
    prior = _with_shared_index(prior, observed=T0)
    before = catalog_to_json(prior)

    added = _item(slug="new")
    current, _ = reconcile_discovery(prior, [added, *items], now=T1)
    current = _with_shared_index(current, observed=T1)
    after = catalog_to_json(current)

    before_payload = json.loads(before)
    after_payload = json.loads(after)
    for payload in (before_payload, after_payload):
        for entry in payload["entries"].values():
            assert "position" not in entry
            assert "last_seen_at" not in entry

    edits = _json_edits(before, after)
    assert not any("position" in line for line in edits)
    # One inserted object (plus entry_order / trailing-comma noise), not O(n)
    # position or last_seen_at rewrites across the catalog.
    assert len(edits) < 80
    summary = catalog_material_summary(prior, current)
    assert summary == f"added=1 removed=0 changed=0 ids={added.stable_id}"


def test_aud_021_material_summary_caps_id_list() -> None:
    ids = [f"https://paulgraham.com/e{i}.html" for i in range(12)]
    current = Catalog(
        schema_version=3,
        material_config_fingerprint="fp",
        entry_order=ids,
        entries={
            sid: CatalogEntry(stable_id=sid, url=sid, title=sid, position=i)
            for i, sid in enumerate(ids)
        },
    )
    summary = catalog_material_summary(None, current)
    assert summary.startswith("added=12 removed=0 changed=0 ids=")
    assert ",...(+4)" in summary
    assert summary.count("https://paulgraham.com/") == 8


def test_aud_021_load_v2_with_positions_saved_v3_omits_position() -> None:
    a = "https://paulgraham.com/a.html"
    b = "https://paulgraham.com/b.html"
    raw = {
        "schema_version": 2,
        "material_config_fingerprint": "fp",
        "index": {"last_success_at": "2024-01-01T12:00:00Z"},
        "entry_order": [a, b],
        "entries": {
            a: {
                "stable_id": a,
                "url": a,
                "title": "A",
                "position": 0,
                "last_seen_at": "2024-01-01T12:00:00Z",
            },
            b: {
                "stable_id": b,
                "url": b,
                "title": "B",
                "position": 1,
                "last_seen_at": "2024-01-01T12:00:00Z",
            },
        },
    }
    loaded = migrate_catalog(raw)
    assert loaded.schema_version == CATALOG_SCHEMA_VERSION
    assert loaded.entries[a].position == 0
    assert loaded.entries[b].position == 1
    assert loaded.entries[a].last_seen_at == loaded.index.last_success_at

    saved = json.loads(catalog_to_json(loaded))
    assert saved["schema_version"] == 3
    assert "position" not in saved["entries"][a]
    assert "position" not in saved["entries"][b]
    assert "last_seen_at" not in saved["entries"][a]
    assert "last_seen_at" not in saved["entries"][b]
    assert loaded.migration_history[-1]["name"] == "compact_catalog_diffs"
