"""F-001: index-only no-op is an invalid cache contract — catalog planner wins."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from paul_graham_essay_feeds.catalog import ChangeSet, plan_refresh
from paul_graham_essay_feeds.feeds import render_atom, render_json, render_rss, write_feeds
from paul_graham_essay_feeds.models import (
    Catalog,
    CatalogEntry,
    Essay,
    FeedEntrySnapshot,
    FeedSnapshot,
    Lifecycle,
    ResourceState,
    content_sha256,
)
from paul_graham_essay_feeds.pipeline import _should_skip_publish


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
def test_page_only_summary_change_must_not_skip(tmp_path: Path) -> None:
    """Contract: material page/summary change requires a non-skip refresh path.

    Legacy index-hash skip is gone; the catalog refresh planner (Wave 2) is the
    correct gate and must fetch when summary is missing or page state is stale.
    """
    e1 = _essay(summary="Original good summary text here.")
    now = datetime(2024, 1, 1, tzinfo=UTC)
    index_html = "<html>index-v1</html>"
    index_hash = content_sha256(index_html)
    fp = e1.index_fingerprint()
    snap = FeedSnapshot(
        logical_updated_at=now,
        generator="pg-essay-feeds/test",
        index_hash=index_hash,
        index_fingerprint=fp,
        items=[
            FeedEntrySnapshot(
                id=e1.stable_id,
                url=e1.url,
                title=e1.title,
                summary=e1.summary or e1.title,
                observed_updated_at=now,
            )
        ],
    )
    write_feeds(
        tmp_path,
        rss=render_rss(snap),
        atom=render_atom(snap),
        json_feed=render_json(snap),
    )
    e2 = _essay(summary="Updated page-derived summary that should publish.")
    assert e2.index_fingerprint() == fp  # index fields unchanged

    catalog = Catalog(
        schema_version=1,
        material_config_fingerprint="test",
        entry_order=[e1.stable_id],
        entries={
            e1.stable_id: CatalogEntry(
                stable_id=e1.stable_id,
                url=e1.url,
                title=e1.title,
                position=0,
                lifecycle=Lifecycle.ACTIVE,
                first_seen_at=now,
                last_seen_at=now,
                observed_updated_at=now,
                summary=None,  # missing page metadata → must refresh
                page=ResourceState(last_checked_at=None),
            )
        },
    )
    plan = plan_refresh(
        catalog,
        force=False,
        enrich=True,
        stale_after_days=30,
        now=now,
    )
    assert any(d.fetch_page for d in plan.decisions)
    # Pointer missing → never skip first generation publish.
    assert not _should_skip_publish(
        root=tmp_path,
        force=False,
        changeset=ChangeSet(),
        plan=plan,
    )
