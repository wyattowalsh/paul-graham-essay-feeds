"""F-013: published feed files should be world-readable (0644 subject to umask)."""

from __future__ import annotations

import os
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from paul_graham_essay_feeds.feeds import render_atom, render_json, render_rss, write_feeds
from paul_graham_essay_feeds.model import Essay


def _essay() -> Essay:
    return Essay.model_validate(
        {
            "position": 1,
            "title": "Hello",
            "url": "https://paulgraham.com/hello.html",
            "stable_id": "https://paulgraham.com/hello.html",
            "is_permalink": True,
            "summary": "A short summary for mode tests.",
        }
    )


@pytest.mark.characterization
@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits only")
def test_written_feeds_are_group_and_other_readable(tmp_path: Path) -> None:
    essays = [_essay()]
    now = datetime(2024, 1, 1, tzinfo=UTC)
    write_feeds(
        tmp_path,
        rss=render_rss(essays, built_at=now),
        atom=render_atom(essays, built_at=now),
        json_feed=render_json(essays, built_at=now),
    )
    for name in ("rss.xml", "atom.xml", "feed.json"):
        mode = (tmp_path / "feeds" / name).stat().st_mode
        assert mode & stat.S_IRGRP, f"{name} not group-readable: {oct(mode)}"
        assert mode & stat.S_IROTH, f"{name} not other-readable: {oct(mode)}"
