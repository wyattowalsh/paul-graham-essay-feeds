"""Shared pytest fixtures."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from paul_graham_essay_feeds.domain import EssayItem, PublicUrls, make_stable_id
from tests.html_samples import synthetic_index_html

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def repo_root() -> Path:
    return ROOT


@pytest.fixture
def fixture_html(tmp_path: Path) -> Path:
    """Offline index HTML for update/build tests (no repo fixtures/ tree)."""
    path = tmp_path / "articles.html"
    path.write_text(synthetic_index_html(), encoding="utf-8")
    return path


@pytest.fixture
def public_base() -> str:
    return "https://example.test/paul-graham-essay-feeds/"


@pytest.fixture
def public_urls(public_base: str) -> PublicUrls:
    return PublicUrls.from_base(public_base)


@pytest.fixture
def observed_at() -> datetime:
    return datetime(2026, 7, 11, 7, 24, 19, tzinfo=UTC)


@pytest.fixture
def sample_items(observed_at: datetime) -> tuple[EssayItem, ...]:
    def make(pos: int, slug: str, title: str) -> EssayItem:
        url = f"https://paulgraham.com/{slug}.html"
        sid, perm = make_stable_id(url)
        return EssayItem(pos, title, url, sid, perm, observed_at, observed_at)

    return (
        make(1, "a", "Alpha"),
        make(2, "b", "Beta"),
        make(3, "c", "Gamma"),
    )


@pytest.fixture
def tmp_repo(tmp_path: Path, public_base: str) -> Path:
    """Minimal repo layout for CLI/build tests."""
    (tmp_path / "data").mkdir()
    (tmp_path / "feeds").mkdir()
    (tmp_path / "reports").mkdir()
    config = f'''
[source]
url = "https://paulgraham.com/articles.html"
minimum_items = 1
max_response_bytes = 5242880
retries = 1

[feed]
title = "Paul Graham: Essays"
description = "Test description"
author_name = "Paul Graham"
author_url = "https://paulgraham.com/"
language = "en"
home_page_url = "https://paulgraham.com/articles.html"

[deployment]
public_base_url = "{public_base}"

[outputs]
rss = "feeds/rss.xml"
atom = "feeds/atom.xml"
json_feed = "feeds/feed.json"
opml = "feeds/subscriptions.opml"
items = "data/essays.json"
state = "data/state.json"
validation = "reports/validation.json"
checksums = "SHA256SUMS"

[policy]
allow_removals = false
allow_nonprefix_additions = false
backup_count = 1
'''
    (tmp_path / "config.toml").write_text(config, encoding="utf-8")
    return tmp_path
