"""Reconciliation policy tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from paul_graham_essay_feeds.domain import EssayItem, FeedError, make_stable_id
from paul_graham_essay_feeds.reconcile import reconcile_items

NOW = datetime(2026, 7, 11, 7, 24, 19, tzinfo=UTC)


def _item(position: int, slug: str) -> EssayItem:
    url = f"https://paulgraham.com/{slug}.html"
    sid, perm = make_stable_id(url)
    return EssayItem(position, slug, url, sid, perm, NOW, NOW)


def test_new_prefix_is_accepted() -> None:
    old = (_item(1, "a"), _item(2, "b"))
    current = (_item(1, "new"), _item(2, "a"), _item(3, "b"))
    changes = reconcile_items(old, current, allow_removals=False, allow_nonprefix_additions=False)
    assert len(changes.added) == 1
    assert not changes.removed


def test_nonprefix_addition_is_rejected() -> None:
    old = (_item(1, "a"), _item(2, "b"))
    current = (_item(1, "a"), _item(2, "new"), _item(3, "b"))
    with pytest.raises(FeedError):
        reconcile_items(old, current, allow_removals=False, allow_nonprefix_additions=False)


def test_removal_is_rejected() -> None:
    old = (_item(1, "a"), _item(2, "b"))
    current = (_item(1, "a"),)
    with pytest.raises(FeedError):
        reconcile_items(old, current, allow_removals=False, allow_nonprefix_additions=False)


def test_title_change_reported() -> None:
    old = (_item(1, "a"),)
    new = EssayItem(
        1,
        "changed",
        old[0].url,
        old[0].stable_id,
        True,
        NOW,
        NOW,
    )
    changes = reconcile_items(old, (new,), allow_removals=False, allow_nonprefix_additions=False)
    assert changes.title_changed == (old[0].stable_id,)
