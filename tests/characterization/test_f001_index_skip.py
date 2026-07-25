"""F-001: index-only no-op is an invalid cache contract (catalog deferred)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from paul_graham_essay_feeds.cli import _should_skip_update
from paul_graham_essay_feeds.feeds import render_atom, render_json, render_rss, write_feeds
from paul_graham_essay_feeds.model import Essay, content_sha256


def _essay(title: str = "A", summary: str = "Summary A for the essay.") -> Essay:
    return Essay.model_validate(
        {
            "position": 1,
            "title": title,
            "url": "https://paulgraham.com/a.html",
            "stable_id": "https://paulgraham.com/a.html",
            "is_permalink": True,
            "summary": summary,
        }
    )


@pytest.mark.characterization
@pytest.mark.xfail(
    strict=True,
    reason="F-001: skip still ignores enrichment/page-only changes (needs catalog planner)",
)
def test_page_only_summary_change_must_not_skip(tmp_path: Path) -> None:
    """Contract: material page/summary change requires a non-skip refresh path."""
    e1 = _essay(summary="Original good summary text here.")
    now = datetime(2024, 1, 1, tzinfo=UTC)
    index_html = "<html>index-v1</html>"
    index_hash = content_sha256(index_html)
    fp = e1.index_fingerprint()
    write_feeds(
        tmp_path,
        rss=render_rss([e1], built_at=now),
        atom=render_atom([e1], built_at=now),
        json_feed=render_json(
            [e1],
            built_at=now,
            index_hash=index_hash,
            index_fingerprint=fp,
        ),
    )
    e2 = _essay(summary="Updated page-derived summary that should publish.")
    assert e2.index_fingerprint() == fp  # index fields unchanged
    # Same index identity → current code skips; contract forbids skip when summary material changed.
    assert not _should_skip_update(
        root=tmp_path,
        index_hash=index_hash,
        essays=[e2],
        force=False,
    )
