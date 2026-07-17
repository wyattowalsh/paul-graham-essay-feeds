"""Unit tests for write.py."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from paul_graham_essay_feeds.feeds import (
    _atomic_write,
    feed_paths,
    render_atom,
    render_json,
    render_rss,
    write_feeds,
)
from paul_graham_essay_feeds.model import Essay, utc_now


def _sample() -> list[Essay]:
    return [
        Essay(
            position=1,
            title="A",
            url="https://paulgraham.com/a.html",
            stable_id="https://paulgraham.com/a.html",
            is_permalink=True,
        ),
        Essay(
            position=2,
            title="B",
            url="https://paulgraham.com/b.html",
            stable_id="https://paulgraham.com/b.html",
            is_permalink=True,
        ),
    ]


def test_write_feeds_creates_expected_paths(repo_root: Path) -> None:
    essays = _sample()
    now = utc_now()
    write_feeds(
        repo_root,
        rss=render_rss(essays, built_at=now),
        atom=render_atom(essays, built_at=now),
        json_feed=render_json(essays),
        essays=essays,
    )
    paths = feed_paths(repo_root)
    assert paths["rss"].is_file()
    catalog = json.loads((repo_root / "data" / "essays.json").read_text(encoding="utf-8"))
    assert catalog["count"] == 2
    assert catalog["items"][0]["title"] == "A"


def test_write_feeds_overwrites(repo_root: Path) -> None:
    essays = _sample()
    now = utc_now()
    write_feeds(
        repo_root,
        rss=render_rss(essays, built_at=now),
        atom=b"<feed/>",
        json_feed=b"{}",
        essays=essays,
    )
    write_feeds(
        repo_root,
        rss=render_rss(essays[:1], built_at=now),
        atom=b"<feed/>",
        json_feed=b"{}",
        essays=essays[:1],
    )
    rss = (repo_root / "feeds" / "rss.xml").read_bytes()
    assert rss.count(b"<item>") == 1
    assert b"<title>A</title>" in rss
    assert b"<title>B</title>" not in rss
    catalog = json.loads((repo_root / "data" / "essays.json").read_text(encoding="utf-8"))
    assert catalog["count"] == 1


def test_feed_paths_keys(repo_root: Path) -> None:
    assert set(feed_paths(repo_root)) == {"rss", "atom", "json"}


def test_atomic_write_cleans_tmp_on_failure(repo_root: Path) -> None:
    target = repo_root / "feeds" / "x.xml"
    with (
        patch("paul_graham_essay_feeds.feeds.os.replace", side_effect=OSError("disk")),
        pytest.raises(OSError, match="disk"),
    ):
        _atomic_write(target, b"data")
    assert list((repo_root / "feeds").glob(".x.xml.*")) == []
