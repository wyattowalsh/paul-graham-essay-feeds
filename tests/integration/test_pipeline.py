"""Integration: extract → render → write without going through argparse."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from paul_graham_essay_feeds.extract import extract_essays
from paul_graham_essay_feeds.feeds import render_atom, render_json, render_rss, write_feeds
from paul_graham_essay_feeds.model import MIN_ITEMS, utc_now

pytestmark = pytest.mark.integration


def test_pipeline_offline(repo_root: Path, sample_html: str) -> None:
    essays = extract_essays(sample_html, min_items=MIN_ITEMS)
    now = utc_now()
    write_feeds(
        repo_root,
        rss=render_rss(essays, built_at=now),
        atom=render_atom(essays, built_at=now),
        json_feed=render_json(essays, built_at=now),
    )
    rss = (repo_root / "feeds" / "rss.xml").read_text(encoding="utf-8")
    atom = (repo_root / "feeds" / "atom.xml").read_text(encoding="utf-8")
    data = json.loads((repo_root / "feeds" / "feed.json").read_text(encoding="utf-8"))
    n = len(essays)
    assert n >= MIN_ITEMS
    assert rss.count("<item>") == n
    assert atom.count("<entry>") == n
    assert len(data["items"]) == n
    assert data["items"][0]["url"] == essays[0].url
    assert not (repo_root / "data" / "essays.json").exists()
