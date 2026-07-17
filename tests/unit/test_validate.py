"""Unit tests for validate.py."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import httpx
import pytest
import respx

from paul_graham_essay_feeds.model import Essay, FeedError
from paul_graham_essay_feeds.validate import (
    validate_essays_live,
    validate_essays_structural,
)


@pytest.fixture(autouse=True)
def _no_tenacity_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero wall-clock waits so retry paths stay fast and deterministic.

    Tenacity binds ``nap.sleep`` as a default arg on ``Retrying``, so patching
    ``tenacity.nap.sleep`` alone does not affect already-constructed controllers.
    Force ``wait_none`` and a no-op ``sleep`` after construction.
    """
    from tenacity import wait_none

    from paul_graham_essay_feeds import fetch as fetch_mod

    original = fetch_mod.retrying

    def _fast_retrying(*, attempts: int, reraise: bool = True):
        controller = original(attempts=attempts, reraise=reraise)
        controller.wait = wait_none()
        controller.sleep = lambda _seconds: None
        return controller

    monkeypatch.setattr(fetch_mod, "retrying", _fast_retrying)


def _essay(url: str = "https://paulgraham.com/a.html") -> Essay:
    return Essay(
        position=1,
        title="A",
        url=url,
        stable_id=url,
        is_permalink=True,
    )


def test_structural_ok() -> None:
    validate_essays_structural([_essay()])


@respx.mock
def test_live_probe_ok() -> None:
    respx.head("https://paulgraham.com/a.html").mock(return_value=httpx.Response(200))
    validate_essays_live([_essay()], timeout=2.0, workers=2)


@respx.mock
def test_live_probe_failure() -> None:
    respx.head("https://paulgraham.com/a.html").mock(return_value=httpx.Response(404))
    with pytest.raises(FeedError, match="link probe"):
        validate_essays_live([_essay()], timeout=2.0, workers=2)


@respx.mock
def test_live_probe_head_not_allowed_falls_back_to_get() -> None:
    respx.head("https://paulgraham.com/a.html").mock(return_value=httpx.Response(405))
    respx.get("https://paulgraham.com/a.html").mock(return_value=httpx.Response(200))
    validate_essays_live([_essay()], timeout=2.0, workers=2)


@respx.mock
def test_live_probe_head_501_falls_back_to_get() -> None:
    respx.head("https://paulgraham.com/a.html").mock(return_value=httpx.Response(501))
    respx.get("https://paulgraham.com/a.html").mock(return_value=httpx.Response(200))
    validate_essays_live([_essay()], timeout=2.0, workers=2)


@respx.mock
def test_live_probe_transport_error() -> None:
    respx.head("https://paulgraham.com/a.html").mock(side_effect=httpx.ConnectError("nope"))
    with pytest.raises(FeedError, match="link probe"):
        validate_essays_live([_essay()], timeout=2.0, workers=2)


@respx.mock
def test_live_probe_redirect_disallowed() -> None:
    respx.head("https://paulgraham.com/a.html").mock(
        return_value=httpx.Response(302, headers={"Location": "https://evil.example/x"})
    )
    with pytest.raises(FeedError, match="link probe"):
        validate_essays_live([_essay()], timeout=2.0, workers=2, retries=0)


@respx.mock
def test_live_probe_workers_kwarg_honored() -> None:
    respx.head("https://paulgraham.com/a.html").mock(return_value=httpx.Response(200))
    with patch(
        "paul_graham_essay_feeds.validate.ThreadPoolExecutor",
        wraps=ThreadPoolExecutor,
    ) as pool_cls:
        validate_essays_live([_essay()], timeout=2.0, workers=3, retries=0)
    assert pool_cls.call_args is not None
    assert pool_cls.call_args.kwargs["max_workers"] == 3


@respx.mock
def test_live_probe_max_bytes_on_get_fallback() -> None:
    respx.head("https://paulgraham.com/a.html").mock(return_value=httpx.Response(405))
    respx.get("https://paulgraham.com/a.html").mock(
        return_value=httpx.Response(200, content=b"x" * 200)
    )
    with pytest.raises(FeedError, match="link probe"):
        validate_essays_live([_essay()], timeout=2.0, workers=1, retries=0, max_bytes=50)


@respx.mock
def test_live_probe_head_content_length_over_max_bytes() -> None:
    """HEAD Content-Length over max_bytes fails closed (same budget as GET)."""
    respx.head("https://paulgraham.com/a.html").mock(
        return_value=httpx.Response(
            200,
            content=b"tiny",
            headers={"Content-Length": "99999"},
        )
    )
    with pytest.raises(FeedError, match="link probe"):
        validate_essays_live([_essay()], timeout=2.0, workers=1, retries=0, max_bytes=50)


@respx.mock
def test_live_probe_head_body_over_max_bytes() -> None:
    """HEAD response body over max_bytes fails closed."""
    respx.head("https://paulgraham.com/a.html").mock(
        return_value=httpx.Response(200, content=b"x" * 200)
    )
    with pytest.raises(FeedError, match="link probe"):
        validate_essays_live([_essay()], timeout=2.0, workers=1, retries=0, max_bytes=50)


@respx.mock
def test_live_probe_redirect_to_loopback_rejected() -> None:
    """Non-loopback essay URL must not bypass when Location is loopback."""
    respx.head("https://paulgraham.com/a.html").mock(
        return_value=httpx.Response(
            302,
            headers={"Location": "http://127.0.0.1/secret"},
        )
    )
    with pytest.raises(FeedError, match="link probe"):
        validate_essays_live([_essay()], timeout=2.0, workers=1, retries=0)


@respx.mock
def test_live_probe_error_preview_plus_n_more() -> None:
    """Aggregate failures show first 10 lines plus ``+N more`` overflow."""
    essays = [
        Essay(
            position=i,
            title=f"E{i}",
            url=f"https://paulgraham.com/e{i}.html",
            stable_id=f"https://paulgraham.com/e{i}.html",
            is_permalink=True,
        )
        for i in range(1, 13)
    ]
    for essay in essays:
        respx.head(essay.url).mock(return_value=httpx.Response(404))
    with pytest.raises(FeedError, match=r"12 link probe failure\(s\):") as exc_info:
        validate_essays_live(essays, timeout=2.0, workers=4, retries=0)
    msg = str(exc_info.value)
    assert "+2 more" in msg
    # First 10 failures listed; overflow summarized (not a 12th detail line).
    assert msg.count("→ HTTP 404") == 10


@respx.mock
def test_live_probe_turbify_host_ok() -> None:
    url = "https://sep.turbifycdn.com/ty/cdn/paulgraham/acl1.txt"
    respx.head(url).mock(return_value=httpx.Response(200))
    validate_essays_live([_essay(url)], timeout=2.0, workers=1, retries=0)


@respx.mock
def test_live_probe_allowed_turbify_redirect() -> None:
    """Live probes may hop onto ``sep.turbifycdn.com`` under ALLOWED_HOSTS."""
    respx.head("https://paulgraham.com/a.html").mock(
        return_value=httpx.Response(
            302,
            headers={"Location": "https://sep.turbifycdn.com/ty/cdn/paulgraham/acl1.txt"},
        )
    )
    respx.head("https://sep.turbifycdn.com/ty/cdn/paulgraham/acl1.txt").mock(
        return_value=httpx.Response(200)
    )
    validate_essays_live([_essay()], timeout=2.0, workers=1, retries=0)


@respx.mock
def test_live_probe_relative_redirect_same_host() -> None:
    respx.head("https://paulgraham.com/a.html").mock(
        return_value=httpx.Response(302, headers={"Location": "/b.html"})
    )
    respx.head("https://paulgraham.com/b.html").mock(return_value=httpx.Response(200))
    validate_essays_live([_essay()], timeout=2.0, workers=1, retries=0)


@respx.mock
def test_live_probe_retryable_then_ok() -> None:
    route = respx.head("https://paulgraham.com/a.html")
    route.side_effect = [
        httpx.Response(503, text="busy"),
        httpx.Response(200),
    ]
    validate_essays_live([_essay()], timeout=2.0, workers=1, retries=2)
