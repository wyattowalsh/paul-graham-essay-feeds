"""Unit tests for fetch.py (httpx + respx + Tenacity)."""

from __future__ import annotations

import httpx
import pytest
import respx

from paul_graham_essay_feeds.fetch import (
    _assert_url,
    decode_html,
    fetch_html,
    hop_safe_get,
    hop_safe_request,
)
from paul_graham_essay_feeds.model import ALLOWED_HOSTS, FeedError


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


def test_decode_html_utf8() -> None:
    assert decode_html("café".encode()) == "café"


def test_decode_html_latin1_fallback() -> None:
    assert decode_html(bytes([0xE9])) == "é"


def test_assert_url_https_ok() -> None:
    _assert_url("https://paulgraham.com/articles.html")
    _assert_url("https://www.paulgraham.com/articles.html")


def test_assert_url_loopback_http_ok() -> None:
    _assert_url("http://127.0.0.1:8765/index.html")


def test_assert_url_rejects_http_remote() -> None:
    with pytest.raises(FeedError, match="https"):
        _assert_url("http://paulgraham.com/articles.html")


def test_assert_url_rejects_other_host() -> None:
    with pytest.raises(FeedError, match="not allowed"):
        _assert_url("https://example.com/articles.html")


@respx.mock
def test_fetch_html_ok() -> None:
    respx.get("https://paulgraham.com/articles.html").mock(
        return_value=httpx.Response(200, text="<html>ok</html>")
    )
    html = fetch_html("https://paulgraham.com/articles.html", timeout=5.0, retries=0)
    assert "ok" in html


@respx.mock
def test_fetch_html_http_error_no_retry() -> None:
    respx.get("https://paulgraham.com/articles.html").mock(
        return_value=httpx.Response(404, text="nope")
    )
    with pytest.raises(FeedError, match=r"HTTP 404|failed"):
        fetch_html("https://paulgraham.com/articles.html", timeout=5.0, retries=2)


@respx.mock
def test_fetch_retryable_then_ok() -> None:
    route = respx.get("https://paulgraham.com/articles.html")
    route.side_effect = [
        httpx.Response(503, text="busy"),
        httpx.Response(200, text="<html>ok</html>"),
    ]
    html = fetch_html("https://paulgraham.com/articles.html", timeout=5.0, retries=2)
    assert "ok" in html


@respx.mock
def test_fetch_oversize() -> None:
    respx.get("https://paulgraham.com/articles.html").mock(
        return_value=httpx.Response(200, content=b"x" * 200)
    )
    with pytest.raises(FeedError, match="over"):
        fetch_html(
            "https://paulgraham.com/articles.html",
            timeout=5.0,
            retries=0,
            max_bytes=50,
        )


@respx.mock
def test_fetch_oversize_content_length() -> None:
    """RV-S-003: declared Content-Length over max fails before full body use."""
    respx.get("https://paulgraham.com/articles.html").mock(
        return_value=httpx.Response(
            200,
            content=b"tiny",
            headers={"Content-Length": "99999"},
        )
    )
    with pytest.raises(FeedError, match="over"):
        fetch_html(
            "https://paulgraham.com/articles.html",
            timeout=5.0,
            retries=0,
            max_bytes=50,
        )


@respx.mock
def test_fetch_redirect_disallowed() -> None:
    # Hop-safe: evil Location is rejected before the second request.
    respx.get("https://paulgraham.com/articles.html").mock(
        return_value=httpx.Response(302, headers={"Location": "https://evil.example/x"})
    )
    with pytest.raises(FeedError, match=r"not allowed"):
        fetch_html("https://paulgraham.com/articles.html", timeout=5.0, retries=0)


@respx.mock
def test_fetch_redirect_allowed_same_host() -> None:
    respx.get("https://paulgraham.com/articles.html").mock(
        return_value=httpx.Response(
            302, headers={"Location": "https://paulgraham.com/articles2.html"}
        )
    )
    respx.get("https://paulgraham.com/articles2.html").mock(
        return_value=httpx.Response(200, text="<html>here</html>")
    )
    html = fetch_html("https://paulgraham.com/articles.html", timeout=5.0, retries=0)
    assert "here" in html


@respx.mock
def test_fetch_redirect_missing_location() -> None:
    respx.get("https://paulgraham.com/articles.html").mock(
        return_value=httpx.Response(302, headers={})
    )
    with pytest.raises(FeedError, match="Location"):
        fetch_html("https://paulgraham.com/articles.html", timeout=5.0, retries=0)


@respx.mock
def test_fetch_redirect_hop_limit() -> None:
    # Six consecutive redirects on an allowed host → hop limit.
    for i in range(6):
        src = f"https://paulgraham.com/r{i}.html"
        dst = f"https://paulgraham.com/r{i + 1}.html"
        respx.get(src).mock(return_value=httpx.Response(302, headers={"Location": dst}))
    with pytest.raises(FeedError, match="Too many redirects"):
        fetch_html("https://paulgraham.com/r0.html", timeout=5.0, retries=0)


def test_fetch_html_index_rejects_turbify_url() -> None:
    """Index fetch allowlist is paulgraham.com only (not Turbify)."""
    with pytest.raises(FeedError, match="not allowed"):
        fetch_html(
            "https://sep.turbifycdn.com/ty/cdn/paulgraham/acl1.txt",
            timeout=5.0,
            retries=0,
        )


@respx.mock
def test_hop_safe_get_blocks_open_redirect() -> None:
    """Allowed host → off-host Location is rejected before following."""
    respx.get("https://paulgraham.com/a.html").mock(
        return_value=httpx.Response(302, headers={"Location": "https://evil.example/x"})
    )
    with (
        httpx.Client(follow_redirects=False, trust_env=False) as client,
        pytest.raises(FeedError, match="not allowed"),
    ):
        hop_safe_get(
            client,
            "https://paulgraham.com/a.html",
            allowed_hosts=frozenset({"paulgraham.com"}),
            max_bytes=1024,
        )


@respx.mock
def test_hop_safe_get_relative_location() -> None:
    """Relative Location is resolved against the current hop URL."""
    respx.get("https://paulgraham.com/a.html").mock(
        return_value=httpx.Response(302, headers={"Location": "/b.html"})
    )
    respx.get("https://paulgraham.com/b.html").mock(
        return_value=httpx.Response(200, text="<html>rel</html>")
    )
    with httpx.Client(follow_redirects=False, trust_env=False) as client:
        response = hop_safe_get(
            client,
            "https://paulgraham.com/a.html",
            allowed_hosts=frozenset({"paulgraham.com"}),
            max_bytes=1024,
        )
    assert "rel" in response.text


@respx.mock
def test_hop_safe_get_www_normalized_mid_chain() -> None:
    """www.paulgraham.com is treated as the same host as paulgraham.com."""
    respx.get("https://paulgraham.com/a.html").mock(
        return_value=httpx.Response(302, headers={"Location": "https://www.paulgraham.com/b.html"})
    )
    respx.get("https://www.paulgraham.com/b.html").mock(
        return_value=httpx.Response(200, text="<html>www</html>")
    )
    with httpx.Client(follow_redirects=False, trust_env=False) as client:
        response = hop_safe_get(
            client,
            "https://paulgraham.com/a.html",
            allowed_hosts=frozenset({"paulgraham.com"}),
            max_bytes=1024,
        )
    assert "www" in response.text


@respx.mock
def test_hop_safe_get_www_host_normalized() -> None:
    """``www.paulgraham.com`` start URL is allowlisted as ``paulgraham.com``."""
    respx.get("https://www.paulgraham.com/a.html").mock(
        return_value=httpx.Response(200, text="<html>www</html>")
    )
    with httpx.Client(follow_redirects=False, trust_env=False) as client:
        response = hop_safe_get(
            client,
            "https://www.paulgraham.com/a.html",
            allowed_hosts=frozenset({"paulgraham.com"}),
            max_bytes=1024,
        )
    assert "www" in response.text


@respx.mock
def test_hop_safe_get_mid_chain_deny() -> None:
    """First hop allowed; second Location leaves the allowlist."""
    respx.get("https://paulgraham.com/a.html").mock(
        return_value=httpx.Response(302, headers={"Location": "https://paulgraham.com/b.html"})
    )
    respx.get("https://paulgraham.com/b.html").mock(
        return_value=httpx.Response(302, headers={"Location": "https://evil.example/x"})
    )
    with (
        httpx.Client(follow_redirects=False, trust_env=False) as client,
        pytest.raises(FeedError, match="not allowed"),
    ):
        hop_safe_get(
            client,
            "https://paulgraham.com/a.html",
            allowed_hosts=frozenset({"paulgraham.com"}),
            max_bytes=1024,
        )


@respx.mock
def test_hop_safe_get_rejects_http_remote_redirect() -> None:
    """http:// non-loopback mid-chain Location is rejected."""
    respx.get("https://paulgraham.com/a.html").mock(
        return_value=httpx.Response(302, headers={"Location": "http://paulgraham.com/b.html"})
    )
    with (
        httpx.Client(follow_redirects=False, trust_env=False) as client,
        pytest.raises(FeedError, match="https"),
    ):
        hop_safe_get(
            client,
            "https://paulgraham.com/a.html",
            allowed_hosts=frozenset({"paulgraham.com"}),
            max_bytes=1024,
        )


@respx.mock
def test_hop_safe_get_oversize() -> None:
    respx.get("https://paulgraham.com/a.html").mock(
        return_value=httpx.Response(200, content=b"x" * 200)
    )
    with (
        httpx.Client(follow_redirects=False, trust_env=False) as client,
        pytest.raises(FeedError, match="over"),
    ):
        hop_safe_get(
            client,
            "https://paulgraham.com/a.html",
            allowed_hosts=frozenset({"paulgraham.com"}),
            max_bytes=50,
        )


@respx.mock
def test_hop_safe_get_index_hosts_reject_turbify() -> None:
    """Index allowlist is paulgraham.com-only (Turbify needs ALLOWED_HOSTS)."""
    respx.get("https://sep.turbifycdn.com/ty/cdn/paulgraham/acl1.txt").mock(
        return_value=httpx.Response(200, text="body")
    )
    with (
        httpx.Client(follow_redirects=False, trust_env=False) as client,
        pytest.raises(FeedError, match="not allowed"),
    ):
        hop_safe_get(
            client,
            "https://sep.turbifycdn.com/ty/cdn/paulgraham/acl1.txt",
            allowed_hosts=frozenset({"paulgraham.com"}),
            max_bytes=1024,
        )


@respx.mock
def test_hop_safe_get_allows_turbify_when_listed() -> None:
    url = "https://sep.turbifycdn.com/ty/cdn/paulgraham/acl1.txt"
    respx.get(url).mock(return_value=httpx.Response(200, text="acl"))
    with httpx.Client(follow_redirects=False, trust_env=False) as client:
        response = hop_safe_get(
            client,
            url,
            allowed_hosts=ALLOWED_HOSTS,
            max_bytes=1024,
        )
    assert response.text == "acl"


@respx.mock
def test_hop_safe_request_head() -> None:
    respx.head("https://paulgraham.com/a.html").mock(return_value=httpx.Response(200))
    with httpx.Client(follow_redirects=False, trust_env=False) as client:
        response = hop_safe_request(
            client,
            "HEAD",
            "https://paulgraham.com/a.html",
            allowed_hosts=frozenset({"paulgraham.com"}),
            max_bytes=None,
        )
    assert response.status_code == 200
