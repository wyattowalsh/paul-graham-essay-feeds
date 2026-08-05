"""Unit tests for verify-then-write catalog + feeds publish (pipeline)."""

from __future__ import annotations

import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from paul_graham_essay_feeds.catalog import default_catalog_path, load_catalog
from paul_graham_essay_feeds.feeds import render_atom, render_json, render_rss
from paul_graham_essay_feeds.models import (
    NULL_REPORTER,
    Catalog,
    CatalogEntry,
    Essay,
    FeedEntrySnapshot,
    FeedError,
    FeedSnapshot,
)
from paul_graham_essay_feeds.pipeline import _publish_catalog_and_feeds

T0 = datetime(2024, 1, 1, tzinfo=UTC)


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


def _snapshot(essays: list[Essay]) -> FeedSnapshot:
    return FeedSnapshot(
        logical_updated_at=T0,
        generator="pg-essay-feeds/test",
        items=[
            FeedEntrySnapshot(
                id=e.stable_id,
                url=e.url,
                title=e.title,
                summary=e.summary or e.title,
                observed_updated_at=T0,
                published_at=e.published_at,
            )
            for e in essays
        ],
    )


def _catalog(count: int = 3) -> Catalog:
    entries = [
        CatalogEntry(
            stable_id=f"https://paulgraham.com/e{i}.html",
            url=f"https://paulgraham.com/e{i}.html",
            title=f"Title {i}",
            position=i - 1,
            first_seen_at=T0,
            last_seen_at=T0,
            observed_updated_at=T0,
            summary=f"A short summary for essay number {i} content.",
        )
        for i in range(1, count + 1)
    ]
    return Catalog(
        schema_version=1,
        material_config_fingerprint="test",
        entry_order=[e.stable_id for e in entries],
        entries={e.stable_id: e for e in entries},
    )


def _bytes(count: int = 3) -> tuple[bytes, bytes, bytes]:
    snap = _snapshot([_essay(i) for i in range(1, count + 1)])
    return render_rss(snap), render_atom(snap), render_json(snap)


def test_publish_writes_catalog_and_feeds_after_verify(tmp_path: Path) -> None:
    rss, atom, jf = _bytes(3)
    catalog = _catalog(3)
    published = _publish_catalog_and_feeds(
        tmp_path,
        catalog=catalog,
        rss=rss,
        atom=atom,
        json_feed=jf,
        simple_rss=rss,
        simple_atom=atom,
        simple_json_feed=jf,
        min_items=3,
        reporter=NULL_REPORTER,
    )
    assert published.entry_order == catalog.entry_order
    assert default_catalog_path(tmp_path).is_file()
    assert (tmp_path / "feeds" / "rss.xml").is_file()
    assert (tmp_path / "feeds" / "atom.xml").is_file()
    assert (tmp_path / "feeds" / "feed.json").is_file()
    assert (tmp_path / "feeds" / "rss.simple.xml").is_file()
    assert (tmp_path / "feeds" / "atom.simple.xml").is_file()
    assert (tmp_path / "feeds" / "feed.simple.json").is_file()
    assert not (tmp_path / "state" / "current.json").exists()
    assert not (tmp_path / "state" / "generations").exists()
    loaded = load_catalog(default_catalog_path(tmp_path))
    assert loaded is not None
    assert loaded.entry_order == catalog.entry_order


def test_publish_does_not_write_on_verify_failure(tmp_path: Path) -> None:
    rss, atom, _jf = _bytes(3)
    bad_json = b'{"version":"https://jsonfeed.org/version/1.1","items":[]}'
    with pytest.raises(FeedError):
        _publish_catalog_and_feeds(
            tmp_path,
            catalog=_catalog(3),
            rss=rss,
            atom=atom,
            json_feed=bad_json,
            simple_rss=rss,
            simple_atom=atom,
            simple_json_feed=bad_json,
            min_items=3,
            reporter=NULL_REPORTER,
        )
    assert not default_catalog_path(tmp_path).exists()
    assert not (tmp_path / "feeds" / "rss.xml").exists()


def test_publish_respects_feed_file_mode(tmp_path: Path) -> None:
    from paul_graham_essay_feeds.feeds import write_feeds

    rss, atom, jf = _bytes(3)
    write_feeds(
        tmp_path,
        rss=rss,
        atom=atom,
        json_feed=jf,
        simple_rss=rss,
        simple_atom=atom,
        simple_json_feed=jf,
        file_mode=0o600,
    )
    mode = stat.S_IMODE((tmp_path / "feeds" / "rss.xml").stat().st_mode)
    assert mode == 0o600
