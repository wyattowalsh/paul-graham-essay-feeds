"""F-004/F-005: truthful Atom updated and non-wall-clock feed times."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from paul_graham_essay_feeds.feeds import render_atom, render_rss
from paul_graham_essay_feeds.model import STABLE_UNPUBLISHED_UPDATED, Essay


def _essay(**kwargs: object) -> Essay:
    base = {
        "position": 1,
        "title": "Hello",
        "url": "https://paulgraham.com/hello.html",
        "stable_id": "https://paulgraham.com/hello.html",
        "is_permalink": True,
        "summary": "A short summary for feed tests.",
    }
    base.update(kwargs)
    return Essay.model_validate(base)


@pytest.mark.characterization
@pytest.mark.xfail(strict=True, reason="F-004: Atom entry updated still uses 1970 sentinel")
def test_atom_entry_updated_is_not_epoch_sentinel() -> None:
    essays = [_essay()]
    built = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
    atom = render_atom(essays, built_at=built).decode("utf-8")
    assert "1970-01-01T00:00:00Z" not in atom
    # Desired: entry updated derives from observed material time, never epoch.
    assert STABLE_UNPUBLISHED_UPDATED.isoformat().replace("+00:00", "Z") not in atom


@pytest.mark.characterization
@pytest.mark.xfail(
    strict=True,
    reason="F-005: feed-level timestamps still use invocation wall-clock built_at",
)
def test_feed_level_times_do_not_embed_arbitrary_built_at() -> None:
    essays = [_essay()]
    t1 = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
    t2 = datetime(2024, 12, 31, 23, 59, tzinfo=UTC)
    rss_a = render_rss(essays, built_at=t1)
    rss_b = render_rss(essays, built_at=t2)
    # Desired: identical logical essays → identical feed timestamps / bytes.
    assert rss_a == rss_b
