"""PGF-2026-022 product rematerialize: seven chrome summaries, 234-id bijection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from paul_graham_essay_feeds.catalog import load_catalog
from paul_graham_essay_feeds.feeds import all_feed_paths, verify_feed_artifacts
from paul_graham_essay_feeds.models import (
    GENERATOR,
    MIN_ITEMS,
    Catalog,
    blurb,
    require_generation_id,
)
from paul_graham_essay_feeds.verify import summary_passes_semantic_gate

_REPO = Path(__file__).resolve().parents[2]

# Catalog-confirmed chrome rows (ideas.html, not the passing startupideas.html).
_CHROME_IDS: tuple[str, ...] = (
    "https://paulgraham.com/before.html",
    "https://paulgraham.com/organic.html",
    "https://paulgraham.com/notnot.html",
    "https://paulgraham.com/startupmistakes.html",
    "https://paulgraham.com/ideas.html",
    "https://paulgraham.com/start.html",
    "https://paulgraham.com/wealth.html",
)
_CHROME_SUMMARIES: tuple[str, ...] = (
    "Arabic Translation",
    "? Get funded by Y Combinator .",
    "Russian Translation Japanese Translation Korean Translation",
    (
        "Japanese Translation Spanish Translation Romanian Translation "
        "Chinese Translation Arabic Translation"
    ),
    (
        "One Specific Idea Romanian Translation Japanese Translation "
        "Traditional Chinese Translation Russian Translation Arabic Translation"
    ),
    (
        "Domain Name Search Turkish Translation Hebrew Translation Russian "
        "Translation Chinese Translation French Translation Japanese "
        "Translation Arabic Translation"
    ),
    (
        "Russian Translation Arabic Translation Spanish Translation "
        "You'll find this essay and 14 others in Hackers & Painters ."
    ),
)
_UNCHANGED_ID = "https://paulgraham.com/earn.html"
_UNCHANGED_OBSERVED = "2026-07-29T23:10:04.709869Z"


def _catalog() -> Catalog:
    catalog = load_catalog(_REPO / "catalog.json")
    assert catalog is not None
    return catalog


def _feed_json(name: str) -> dict[str, Any]:
    payload = json.loads((_REPO / "feeds" / name).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


@pytest.mark.characterization
def test_seven_chrome_summaries_are_title_blurbs() -> None:
    catalog = _catalog()
    assert len(_CHROME_IDS) == 7
    for sid in _CHROME_IDS:
        entry = catalog.entries[sid]
        expected = blurb(entry.title)
        assert entry.summary == expected
        assert entry.prior_good_summary == expected
        assert entry.summary_source == "title"
        assert entry.summary_quality == 1.0
        assert entry.quality_flags == ()
        assert summary_passes_semantic_gate(
            entry.summary,
            score=entry.summary_quality,
            flags=entry.quality_flags,
        )
        assert entry.observed_updated_at is not None
        assert entry.observed_updated_at.isoformat().replace("+00:00", "Z") != _UNCHANGED_OBSERVED
    by_id = {item["id"]: item["summary"] for item in _feed_json("feed.json")["items"]}
    for sid in _CHROME_IDS:
        assert by_id[sid] == blurb(catalog.entries[sid].title)


@pytest.mark.characterization
def test_known_bad_chrome_strings_absent_from_enriched_product() -> None:
    catalog = _catalog()
    summaries = {sid: catalog.entries[sid].summary or "" for sid in catalog.entry_order}
    for bad in _CHROME_SUMMARIES:
        assert bad not in summaries.values()
    feed = (_REPO / "feeds" / "feed.json").read_text(encoding="utf-8")
    for bad in _CHROME_SUMMARIES:
        assert bad not in feed
    startupideas = catalog.entries["https://paulgraham.com/startupideas.html"]
    assert startupideas.summary_source != "title"
    assert "way to get startup ideas" in (startupideas.summary or "").lower()


@pytest.mark.characterization
def test_unchanged_entries_keep_observation_clocks() -> None:
    catalog = _catalog()
    entry = catalog.entries[_UNCHANGED_ID]
    assert entry.observed_updated_at is not None
    assert entry.observed_updated_at.isoformat().replace("+00:00", "Z") == _UNCHANGED_OBSERVED
    assert entry.summary_source == "page"
    assert entry.summary_quality == 0.9


@pytest.mark.characterization
def test_identity_order_bijection_234() -> None:
    catalog = _catalog()
    assert len(catalog.entry_order) == 234
    assert len(catalog.entries) == 234
    assert set(catalog.entry_order) == set(catalog.entries)
    assert len(catalog.entry_order) == len(set(catalog.entry_order))
    enriched = [str(item["id"]) for item in _feed_json("feed.json")["items"]]
    simple = [str(item["id"]) for item in _feed_json("feed.simple.json")["items"]]
    assert enriched == catalog.entry_order
    assert simple == catalog.entry_order
    assert len(enriched) == 234
    meta = _feed_json("feed.json")["_pg_essay_feeds"]
    assert meta["item_count"] == 234
    assert meta["generator"] == GENERATOR


@pytest.mark.characterization
def test_last_generation_id_stamped_and_check_passes() -> None:
    catalog = _catalog()
    gen_id = require_generation_id(catalog.last_generation_id or "")
    on_disk = json.loads((_REPO / "catalog.json").read_text(encoding="utf-8"))
    assert on_disk["last_generation_id"] == gen_id
    assert on_disk["schema_version"] == 3
    verify_feed_artifacts(_REPO, min_items=MIN_ITEMS)
    paths = all_feed_paths(_REPO)
    assert all(path.is_file() for path in paths.values())
