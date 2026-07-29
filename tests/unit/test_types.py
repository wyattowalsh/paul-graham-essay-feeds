"""Unit tests for types helpers."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from paul_graham_essay_feeds.models import FeedError, normalize_essay_url, require_aware_utc


def test_require_aware_utc_rejects_naive() -> None:
    with pytest.raises(FeedError, match="Naive"):
        require_aware_utc(datetime(2020, 1, 1, 12, 0, 0))


def test_require_aware_utc_normalizes() -> None:
    value = datetime(2020, 1, 1, 12, 0, tzinfo=UTC)
    assert require_aware_utc(value) == value


def test_normalize_www() -> None:
    url = normalize_essay_url("https://www.paulgraham.com/foo.html#frag")
    assert url == "https://paulgraham.com/foo.html"


def test_normalize_rejects_http_non_loopback() -> None:
    with pytest.raises(FeedError):
        normalize_essay_url("http://paulgraham.com/x.html")


def test_normalize_allows_loopback_http() -> None:
    url = normalize_essay_url("http://127.0.0.1:9/x", allow_loopback=True)
    assert url.startswith("http://127.0.0.1")


def test_normalize_rejects_disallowed_host() -> None:
    with pytest.raises(FeedError, match="not allowed"):
        normalize_essay_url("https://evil.example/x.html")


def test_normalize_rejects_userinfo() -> None:
    with pytest.raises(FeedError, match="userinfo"):
        normalize_essay_url("https://user:pass@paulgraham.com/x.html")


def test_normalize_rejects_relative() -> None:
    with pytest.raises(FeedError, match="absolute"):
        normalize_essay_url("/relative.html")


def test_normalize_rejects_loopback_without_flag() -> None:
    with pytest.raises(FeedError, match="Loopback"):
        normalize_essay_url("http://127.0.0.1/x")
