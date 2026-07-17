"""Integration: extract → render → write without going through argparse."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from paul_graham_essay_feeds.extract import extract_essays
from paul_graham_essay_feeds.feeds import render_atom, render_json, render_rss, write_feeds
from paul_graham_essay_feeds.model import utc_now

pytestmark = pytest.mark.integration


def test_pipeline_offline(repo_root: Path, sample_html: str) -> None:
    essays = extract_essays(sample_html, min_items=233)
    now = utc_now()
    write_feeds(
        repo_root,
        rss=render_rss(essays, built_at=now),
        atom=render_atom(essays, built_at=now),
        json_feed=render_json(essays),
        essays=essays,
    )
    rss = (repo_root / "feeds" / "rss.xml").read_text(encoding="utf-8")
    atom = (repo_root / "feeds" / "atom.xml").read_text(encoding="utf-8")
    data = json.loads((repo_root / "feeds" / "feed.json").read_text(encoding="utf-8"))
    catalog = json.loads((repo_root / "data" / "essays.json").read_text(encoding="utf-8"))
    assert rss.count("<item>") == 233
    assert atom.count("<entry>") == 233
    assert len(data["items"]) == 233
    assert catalog["count"] == 233
    assert catalog["items"][0]["url"] == essays[0].url
