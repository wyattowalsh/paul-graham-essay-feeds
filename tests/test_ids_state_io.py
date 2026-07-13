"""Stable IDs, timestamp merge, and atomic I/O tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from paul_graham_essay_feeds.domain import EssayItem, make_stable_id
from paul_graham_essay_feeds.io import atomic_write, publish_artifacts
from paul_graham_essay_feeds.state import merge_items

NOW = datetime(2026, 7, 11, 7, 24, 19, tzinfo=UTC)
LATER = datetime(2026, 7, 12, 0, 0, 0, tzinfo=UTC)


def test_turbify_stable_id_ignores_query() -> None:
    a, pa = make_stable_id("https://sep.turbifycdn.com/ty/cdn/paulgraham/acl1.txt?t=1")
    b, pb = make_stable_id("https://sep.turbifycdn.com/ty/cdn/paulgraham/acl1.txt?t=999")
    assert a == b
    assert a == "urn:uuid:77a51534-f696-5417-aa1a-564e98a6901a"
    assert not pa and not pb


def test_merge_preserves_first_seen_and_bumps_on_title_change() -> None:
    url = "https://paulgraham.com/a.html"
    sid, perm = make_stable_id(url)
    prev = (EssayItem(1, "Old", url, sid, perm, NOW, NOW),)
    extracted = (EssayItem(1, "New", url, sid, perm, LATER, LATER),)
    merged = merge_items(prev, extracted, now=LATER)
    assert merged[0].first_seen_at == NOW
    assert merged[0].last_changed_at == LATER
    assert merged[0].title == "New"


def test_publish_only_changed(tmp_path: Path) -> None:
    target = tmp_path / "out.txt"
    atomic_write(target, b"hello\n")
    mtime = target.stat().st_mtime_ns
    written = publish_artifacts({target: b"hello\n"}, only_changed=True, backup=False)
    assert written == []
    assert target.stat().st_mtime_ns == mtime
    written2 = publish_artifacts({target: b"world\n"}, only_changed=True, backup=True)
    assert written2 == [target]
    assert target.read_bytes() == b"world\n"
