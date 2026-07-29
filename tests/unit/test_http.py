"""Unit tests for http.py (httpx + respx + Tenacity)."""

from __future__ import annotations

import httpx
import pytest
import respx

from paul_graham_essay_feeds.http import (
    _assert_url,
    decode_html,
    fetch_html,
    hop_safe_get,
    hop_safe_request,
)
from paul_graham_essay_feeds.models import ALLOWED_HOSTS, FeedError


@pytest.fixture(autouse=True)
def _no_tenacity_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero wall-clock waits so retry paths stay fast and deterministic.

    Tenacity binds ``nap.sleep`` as a default arg on ``Retrying``, so patching
    ``tenacity.nap.sleep`` alone does not affect already-constructed controllers.
    Force ``wait_none`` and a no-op ``sleep`` after construction.
    """
    from tenacity import wait_none

    from paul_graham_essay_feeds import http as http_mod

    original = http_mod.retrying

    def _fast_retrying(*, attempts: int, reraise: bool = True):
        controller = original(attempts=attempts, reraise=reraise)
        controller.wait = wait_none()
        controller.sleep = lambda _seconds: None
        return controller

    monkeypatch.setattr(http_mod, "retrying", _fast_retrying)


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


@respx.mock
def test_hop_safe_request_non_loopback_redirect_to_127_feed_error() -> None:
    """F1: non-loopback start → mid-hop 127.0.0.1 is rejected (start-bound allow_loopback)."""
    respx.get("https://paulgraham.com/a.html").mock(
        return_value=httpx.Response(302, headers={"Location": "http://127.0.0.1:9/secret"})
    )
    with (
        httpx.Client(follow_redirects=False, trust_env=False) as client,
        pytest.raises(FeedError, match="not allowed"),
    ):
        hop_safe_request(
            client,
            "GET",
            "https://paulgraham.com/a.html",
            allowed_hosts=frozenset({"paulgraham.com"}),
            max_bytes=1024,
        )


@respx.mock
def test_hop_safe_request_forces_no_auto_follow_on_misconfigured_client() -> None:
    """client.send must not inherit Client(follow_redirects=True) — SSRF before allowlist."""
    start = respx.get("https://paulgraham.com/a.html").mock(
        return_value=httpx.Response(302, headers={"Location": "https://evil.example/x"})
    )
    evil = respx.get("https://evil.example/x").mock(return_value=httpx.Response(200, text="nope"))
    with (
        httpx.Client(follow_redirects=True, trust_env=False) as client,
        pytest.raises(FeedError, match="not allowed"),
    ):
        hop_safe_request(
            client,
            "GET",
            "https://paulgraham.com/a.html",
            allowed_hosts=frozenset({"paulgraham.com"}),
            max_bytes=1024,
        )
    assert start.called
    assert not evil.called


@respx.mock
def test_hop_safe_request_non_loopback_redirect_to_localhost_feed_error() -> None:
    """F1: non-loopback start → mid-hop localhost is rejected."""
    respx.get("https://paulgraham.com/a.html").mock(
        return_value=httpx.Response(302, headers={"Location": "http://localhost:9/secret"})
    )
    with (
        httpx.Client(follow_redirects=False, trust_env=False) as client,
        pytest.raises(FeedError, match="not allowed"),
    ):
        hop_safe_request(
            client,
            "GET",
            "https://paulgraham.com/a.html",
            allowed_hosts=frozenset({"paulgraham.com"}),
            max_bytes=1024,
        )


@respx.mock
def test_hop_safe_request_redirect_close_without_read() -> None:
    """F2: redirect bodies are not required to follow; large 3xx body is ignored."""
    respx.get("https://paulgraham.com/a.html").mock(
        return_value=httpx.Response(
            302,
            content=b"x" * 50_000,
            headers={"Location": "https://paulgraham.com/b.html"},
        )
    )
    respx.get("https://paulgraham.com/b.html").mock(
        return_value=httpx.Response(200, text="<html>final</html>")
    )
    with httpx.Client(follow_redirects=False, trust_env=False) as client:
        response = hop_safe_request(
            client,
            "GET",
            "https://paulgraham.com/a.html",
            allowed_hosts=frozenset({"paulgraham.com"}),
            max_bytes=1024,
        )
    assert "final" in response.text
    # Final body is small; if the 50k redirect body were drained into the capped
    # path, hop_safe_request would have raised FeedError for oversize.
    assert len(response.content) < 1024


@respx.mock
def test_hop_safe_request_multi_hop_empty_redirect_bodies() -> None:
    """F2: multi-hop redirects succeed with empty/minimal 3xx bodies."""
    respx.get("https://paulgraham.com/a.html").mock(
        return_value=httpx.Response(302, headers={"Location": "https://paulgraham.com/b.html"})
    )
    respx.get("https://paulgraham.com/b.html").mock(
        return_value=httpx.Response(
            302, content=b"", headers={"Location": "https://paulgraham.com/c.html"}
        )
    )
    respx.get("https://paulgraham.com/c.html").mock(
        return_value=httpx.Response(200, text="<html>ok</html>")
    )
    with httpx.Client(follow_redirects=False, trust_env=False) as client:
        response = hop_safe_request(
            client,
            "GET",
            "https://paulgraham.com/a.html",
            allowed_hosts=frozenset({"paulgraham.com"}),
            max_bytes=1024,
        )
    assert "ok" in response.text


@respx.mock
def test_hop_safe_request_redirect_oversize_body_ignored() -> None:
    """F2: oversize 3xx body/CL is not size-checked; only the final 200 is capped."""
    respx.get("https://paulgraham.com/a.html").mock(
        return_value=httpx.Response(
            302,
            content=b"x" * 10_000,
            headers={
                "Location": "https://paulgraham.com/b.html",
                "Content-Length": "10000",
            },
        )
    )
    respx.get("https://paulgraham.com/b.html").mock(
        return_value=httpx.Response(200, text="<html>final</html>")
    )
    with httpx.Client(follow_redirects=False, trust_env=False) as client:
        response = hop_safe_request(
            client,
            "GET",
            "https://paulgraham.com/a.html",
            allowed_hosts=frozenset({"paulgraham.com"}),
            max_bytes=100,
        )
    assert "final" in response.text


@respx.mock
def test_hop_safe_request_content_length_oversize_without_buffer() -> None:
    """Content-Length > max_bytes → FeedError (declared size fails closed)."""
    respx.get("https://paulgraham.com/a.html").mock(
        return_value=httpx.Response(
            200,
            content=b"tiny",
            headers={"Content-Length": "99999"},
        )
    )
    with (
        httpx.Client(follow_redirects=False, trust_env=False) as client,
        pytest.raises(FeedError, match="over"),
    ):
        hop_safe_request(
            client,
            "GET",
            "https://paulgraham.com/a.html",
            allowed_hosts=frozenset({"paulgraham.com"}),
            max_bytes=50,
        )


@respx.mock
def test_hop_safe_request_stream_oversize() -> None:
    """Streaming body that exceeds max_bytes raises FeedError."""
    respx.get("https://paulgraham.com/a.html").mock(
        return_value=httpx.Response(200, content=b"x" * 200)
    )
    with (
        httpx.Client(follow_redirects=False, trust_env=False) as client,
        pytest.raises(FeedError, match="over"),
    ):
        hop_safe_request(
            client,
            "GET",
            "https://paulgraham.com/a.html",
            allowed_hosts=frozenset({"paulgraham.com"}),
            max_bytes=50,
        )
