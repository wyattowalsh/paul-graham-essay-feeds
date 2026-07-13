"""Synthetic full-catalog extraction (replaces committed HTML snapshot)."""

from __future__ import annotations

from paul_graham_essay_feeds.domain import SOURCE_URL
from paul_graham_essay_feeds.extract import extract_items
from tests.html_samples import synthetic_index_html


def test_synthetic_catalog_meets_floor_with_turbify_chapters() -> None:
    html = synthetic_index_html(essay_count=231)
    result = extract_items(html, base_url=SOURCE_URL, min_items=233)
    assert result.mode == "essay-row-marker"
    assert len(result.items) == 233
    assert result.items[0].title == "Essay 0"
    assert result.items[0].url == "https://paulgraham.com/essay-0.html"
    turbify = [item for item in result.items if "turbify" in item.url]
    assert len(turbify) == 2
    assert all(not item.is_permalink for item in turbify)
    assert turbify[0].stable_id.startswith("urn:uuid:")
