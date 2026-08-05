"""Settings URL validation (H-14)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from paul_graham_essay_feeds.models import ConfigurationError
from paul_graham_essay_feeds.settings import Settings


def test_public_base_url_rejects_userinfo() -> None:
    with pytest.raises((ValidationError, ConfigurationError)):
        Settings.model_validate(
            {
                "public_base_url": "https://user:pass@example.com/feeds",
            }
        )


def test_public_base_url_rejects_http_non_loopback() -> None:
    with pytest.raises((ValidationError, ConfigurationError)):
        Settings.model_validate({"public_base_url": "http://example.com/feeds"})


def test_public_base_url_https_ok() -> None:
    s = Settings.model_validate(
        {"public_base_url": "https://raw.githubusercontent.com/org/repo/main/feeds"}
    )
    assert s.public_base_url is not None
