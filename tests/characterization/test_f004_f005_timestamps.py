"""F-004/F-005: truthful Atom updated and non-wall-clock feed times."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from paul_graham_essay_feeds.feeds import render_atom, render_rss
from paul_graham_essay_feeds.models import FeedEntrySnapshot, FeedSnapshot, rfc3339

GENERATOR = "pg-essay-feeds/test"


def _snapshot(
    *,
    observed: datetime,
    logical_updated_at: datetime,
) -> FeedSnapshot:
    return FeedSnapshot(
        logical_updated_at=logical_updated_at,
        generator=GENERATOR,
        items=[
            FeedEntrySnapshot(
                id="https://paulgraham.com/hello.html",
                url="https://paulgraham.com/hello.html",
                title="Hello",
                summary="A short summary for feed tests.",
                observed_updated_at=observed,
            )
        ],
    )


@pytest.mark.characterization
def test_atom_entry_updated_is_not_epoch_sentinel() -> None:
    observed = datetime(2024, 5, 15, 10, 30, tzinfo=UTC)
    logical = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
    atom = render_atom(_snapshot(observed=observed, logical_updated_at=logical)).decode("utf-8")
    assert "1970-01-01T00:00:00Z" not in atom
    assert rfc3339(observed) in atom


@pytest.mark.characterization
def test_feed_level_times_come_from_logical_updated_at() -> None:
    """F-005: feed timestamps are snapshot.logical_updated_at, not wall-clock."""
    observed = datetime(2024, 5, 15, 10, 30, tzinfo=UTC)
    logical = datetime(2024, 3, 1, 0, 0, tzinfo=UTC)
    snap = _snapshot(observed=observed, logical_updated_at=logical)
    rss_a = render_rss(snap)
    rss_b = render_rss(snap)
    # Same snapshot → identical bytes (no invocation wall-clock).
    assert rss_a == rss_b
    atom = render_atom(snap).decode("utf-8")
    assert rfc3339(logical) in atom
    # Entry updated stays on observed clock; feed-level uses logical.
    assert rfc3339(observed) in atom
