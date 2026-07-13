"""Full fixture extraction parity against the audited 233-item baseline."""

from __future__ import annotations

from pathlib import Path

from paul_graham_essay_feeds.domain import SOURCE_URL
from paul_graham_essay_feeds.extract import extract_items


def test_fixture_extracts_233_with_boundary_items(fixture_html: Path) -> None:
    html = fixture_html.read_text(encoding="utf-8", errors="replace")
    result = extract_items(html, base_url=SOURCE_URL, min_items=233)
    assert result.mode == "essay-row-marker"
    assert len(result.items) == 233
    assert result.items[0].title == "How to Earn a Billion Dollars"
    assert result.items[0].url == "https://paulgraham.com/earn.html"
    assert result.items[-1].title == ("This Year We Can End the Death Penalty in California")
    assert result.items[-1].url == "https://paulgraham.com/prop62.html"
    turbify = [i for i in result.items if "turbify" in i.url]
    assert len(turbify) == 2
    assert all(not i.is_permalink for i in turbify)
    assert turbify[0].stable_id.startswith("urn:uuid:")
