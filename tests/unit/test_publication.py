"""Unit tests for prevalidated publication."""

from __future__ import annotations

import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from paul_graham_essay_feeds.feeds import render_atom, render_json, render_rss
from paul_graham_essay_feeds.model import Essay, FeedError
from paul_graham_essay_feeds.presentation import NULL_REPORTER
from paul_graham_essay_feeds.publication import publish_feed_bundle, publish_or_raise


def _essay(n: int = 1) -> Essay:
    return Essay.model_validate(
        {
            "position": n,
            "title": f"Title {n}",
            "url": f"https://paulgraham.com/e{n}.html",
            "stable_id": f"https://paulgraham.com/e{n}.html",
            "is_permalink": True,
            "summary": f"A short summary for essay number {n} content.",
        }
    )


def _bytes(count: int = 3) -> tuple[bytes, bytes, bytes]:
    essays = [_essay(i) for i in range(1, count + 1)]
    now = datetime(2024, 1, 1, tzinfo=UTC)
    return (
        render_rss(essays, built_at=now),
        render_atom(essays, built_at=now),
        render_json(essays, built_at=now),
    )


def test_publish_writes_after_verify(tmp_path: Path) -> None:
    rss, atom, jf = _bytes(3)
    result = publish_feed_bundle(
        tmp_path,
        rss=rss,
        atom=atom,
        json_feed=jf,
        min_items=3,
    )
    assert result.report.ok
    assert result.rss_path.is_file()
    assert result.atom_path.is_file()
    assert result.json_path.is_file()


def test_publish_does_not_write_on_verify_failure(tmp_path: Path) -> None:
    rss, atom, _jf = _bytes(3)
    # Corrupt JSON to fail deep verify.
    bad_json = b'{"version":"https://jsonfeed.org/version/1.1","items":[]}'
    with pytest.raises(FeedError):
        publish_feed_bundle(
            tmp_path,
            rss=rss,
            atom=atom,
            json_feed=bad_json,
            min_items=3,
        )
    assert not (tmp_path / "feeds" / "rss.xml").exists()


def test_publish_or_raise_success(tmp_path: Path) -> None:
    rss, atom, jf = _bytes(3)
    result = publish_or_raise(
        tmp_path,
        rss=rss,
        atom=atom,
        json_feed=jf,
        min_items=3,
        reporter=NULL_REPORTER,
        file_mode=0o644,
    )
    assert result.report.ok
    assert result.root == tmp_path
    assert result.rss_path.read_bytes() == rss
    assert result.atom_path.read_bytes() == atom
    assert result.json_path.read_bytes() == jf


def test_publish_or_raise_reraises_feed_error(tmp_path: Path) -> None:
    rss, atom, _jf = _bytes(3)
    bad_json = b'{"version":"https://jsonfeed.org/version/1.1","items":[]}'
    with pytest.raises(FeedError, match="verification failed"):
        publish_or_raise(
            tmp_path,
            rss=rss,
            atom=atom,
            json_feed=bad_json,
            min_items=3,
        )
    feeds_dir = tmp_path / "feeds"
    assert not feeds_dir.exists() or not any(feeds_dir.iterdir())


def test_publish_respects_file_mode(tmp_path: Path) -> None:
    rss, atom, jf = _bytes(3)
    result = publish_feed_bundle(
        tmp_path,
        rss=rss,
        atom=atom,
        json_feed=jf,
        min_items=3,
        file_mode=0o600,
    )
    mode = stat.S_IMODE(result.rss_path.stat().st_mode)
    assert mode == 0o600
