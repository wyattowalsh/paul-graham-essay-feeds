"""Settings URL validation (H-14 / AUD-006)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from paul_graham_essay_feeds.models import ConfigurationError
from paul_graham_essay_feeds.settings import Settings

_INVALID = (ValidationError, ConfigurationError)


def test_public_base_url_rejects_userinfo() -> None:
    with pytest.raises(_INVALID):
        Settings.model_validate(
            {
                "public_base_url": "https://user:pass@example.com/feeds",
            }
        )


def test_public_base_url_rejects_http_non_loopback() -> None:
    with pytest.raises(_INVALID):
        Settings.model_validate({"public_base_url": "http://example.com/feeds"})


def test_public_base_url_https_ok() -> None:
    s = Settings.model_validate(
        {"public_base_url": "https://raw.githubusercontent.com/org/repo/main/feeds"}
    )
    assert s.public_base_url == "https://raw.githubusercontent.com/org/repo/main/feeds/"


@pytest.mark.parametrize(
    "raw",
    [
        "https://example.com/feeds?utm=1",
        "https://example.com/feeds/?x=",
        "https://example.com/feeds?",
    ],
)
def test_public_base_url_rejects_query(raw: str) -> None:
    with pytest.raises(_INVALID, match="query"):
        Settings.model_validate({"public_base_url": raw})


@pytest.mark.parametrize(
    "raw",
    [
        "https://example.com/feeds#section",
        "https://example.com/feeds/#/",
        "https://example.com/feeds#",
    ],
)
def test_public_base_url_rejects_fragment(raw: str) -> None:
    with pytest.raises(_INVALID, match="fragment"):
        Settings.model_validate({"public_base_url": raw})


@pytest.mark.parametrize(
    "raw",
    [
        "https://",
        "https:///feeds",
        "https://:443/feeds",
    ],
)
def test_public_base_url_rejects_empty_hostname(raw: str) -> None:
    with pytest.raises(_INVALID):
        Settings.model_validate({"public_base_url": raw})


def test_public_base_url_rejects_filename_last_segment() -> None:
    with pytest.raises(_INVALID, match="directory"):
        Settings.model_validate({"public_base_url": "https://example.com/feeds/rss.xml"})


def test_public_base_url_trailing_slash_normalized() -> None:
    slash = Settings.model_validate({"public_base_url": "https://example.com/feeds/"})
    noslash = Settings.model_validate({"public_base_url": "https://example.com/feeds"})
    extra = Settings.model_validate({"public_base_url": "https://example.com/feeds///"})
    assert slash.public_base_url == noslash.public_base_url == extra.public_base_url
    assert slash.public_base_url == "https://example.com/feeds/"


def test_public_base_url_idna_host() -> None:
    s = Settings.model_validate({"public_base_url": "https://münchen.example.com/feeds"})
    assert s.public_base_url == "https://xn--mnchen-3ya.example.com/feeds/"


def test_public_base_url_loopback_http_ok() -> None:
    s = Settings.model_validate({"public_base_url": "http://127.0.0.1/feeds"})
    assert s.public_base_url == "http://127.0.0.1/feeds/"
    v6 = Settings.model_validate({"public_base_url": "http://[::1]/feeds/"})
    assert v6.public_base_url == "http://[::1]/feeds/"


def test_public_base_url_blank_becomes_none() -> None:
    s = Settings.model_validate({"public_base_url": "   "})
    assert s.public_base_url is None
