"""Live network tests (opt-in: ``pytest -m live``)."""

from __future__ import annotations

import pytest

from paul_graham_essay_feeds.discover import discover_essays
from paul_graham_essay_feeds.http import fetch_html
from paul_graham_essay_feeds.models import MIN_ITEMS, SOURCE_URL

pytestmark = pytest.mark.live


def test_live_fetch_and_extract() -> None:
    html = fetch_html(SOURCE_URL, timeout=30.0, retries=2)
    essays, _report = discover_essays(html, min_items=MIN_ITEMS)
    assert len(essays) >= MIN_ITEMS
    assert essays[0].url.startswith("https://")
