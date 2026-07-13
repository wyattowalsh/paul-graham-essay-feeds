"""Fail-closed change reconciliation against prior canonical state."""

from __future__ import annotations

from collections.abc import Sequence

from paul_graham_essay_feeds.domain import ChangeSet, EssayItem, FeedError

__all__ = ["reconcile_items"]


def reconcile_items(
    previous: Sequence[EssayItem],
    current: Sequence[EssayItem],
    *,
    allow_removals: bool,
    allow_nonprefix_additions: bool,
) -> ChangeSet:
    """Compare previous and current item sequences under the safety policy.

    Automatically accepts newest-prefix additions. By default rejects removals,
    retained-item reordering, and non-prefix historical insertions.
    """
    old_by_id = {item.identity: item for item in previous}
    new_by_id = {item.identity: item for item in current}
    old_ids = [item.identity for item in previous]
    new_ids = [item.identity for item in current]

    removed = tuple(identity for identity in old_ids if identity not in new_by_id)
    added = tuple(identity for identity in new_ids if identity not in old_by_id)

    if removed and not allow_removals:
        removed_urls = [old_by_id[identity].url for identity in removed]
        raise FeedError(
            "Source reconciliation detected removed items. Refusing to overwrite "
            "without --allow-removals:\n  " + "\n  ".join(removed_urls)
        )

    common_old = [identity for identity in old_ids if identity in new_by_id]
    common_new = [identity for identity in new_ids if identity in old_by_id]
    order_changed = common_old != common_new
    if order_changed:
        raise FeedError("Existing essay order changed unexpectedly. Refusing to overwrite.")

    if added and previous and not allow_nonprefix_additions:
        first_old_index = next(
            (index for index, identity in enumerate(new_ids) if identity in old_by_id),
            len(new_ids),
        )
        nonprefix = [
            identity for identity in new_ids[first_old_index:] if identity not in old_by_id
        ]
        if nonprefix:
            urls = [new_by_id[identity].url for identity in nonprefix]
            raise FeedError(
                "New links appeared inside or after the existing archive rather "
                "than as a newest-item prefix. Refusing to overwrite without "
                "--allow-nonprefix-additions:\n  " + "\n  ".join(urls)
            )

    title_changed = tuple(
        identity
        for identity in common_old
        if old_by_id[identity].title != new_by_id[identity].title
    )
    url_changed = tuple(
        identity for identity in common_old if old_by_id[identity].url != new_by_id[identity].url
    )

    return ChangeSet(
        added=added,
        removed=removed,
        title_changed=title_changed,
        url_changed=url_changed,
        order_changed=order_changed,
    )
