"""Unit tests for transport.py (ADR-004 / F-016)."""

from __future__ import annotations

import hashlib

import httpx
import pytest
import respx

from paul_graham_essay_feeds.model import ALLOWED_HOSTS, FeedError
from paul_graham_essay_feeds.transport import (
    ResultKind,
    get_with_evidence,
    head_with_evidence,
    media_type_is_soft_html,
    parse_content_type,
    request_with_evidence,
)


def test_parse_content_type_html_charset() -> None:
    media, charset = parse_content_type("text/html; charset=UTF-8")
    assert media == "text/html"
    assert charset == "utf-8"


def test_parse_content_type_missing() -> None:
    assert parse_content_type(None) == (None, None)
    assert parse_content_type("") == (None, None)
    assert parse_content_type("bogus") == (None, None)


def test_media_type_soft_html() -> None:
    assert media_type_is_soft_html("text/html") is True
    assert media_type_is_soft_html("TEXT/HTML; charset=utf-8") is True
    assert media_type_is_soft_html("text/plain") is True
    assert media_type_is_soft_html("application/json") is False
    assert media_type_is_soft_html(None) is False


@respx.mock
def test_head_large_content_length_allowed_f016() -> None:
    """F-016: HEAD with Content-Length=50_000_000 and max_bytes=1024 succeeds."""
    respx.head("https://paulgraham.com/big.html").mock(
        return_value=httpx.Response(
            200,
            headers={
                "Content-Length": str(50_000_000),
                "Content-Type": "text/html; charset=utf-8",
                "ETag": '"abc123"',
                "Last-Modified": "Mon, 01 Jan 2024 00:00:00 GMT",
            },
        )
    )
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
        result = request_with_evidence(
            client,
            "HEAD",
            "https://paulgraham.com/big.html",
            allowed_hosts=ALLOWED_HOSTS,
            max_bytes=1024,
        )
    ev = result.evidence
    assert ev.result_kind is ResultKind.FETCHED
    assert ev.status_code == 200
    assert ev.method == "HEAD"
    assert ev.content_length_header == 50_000_000
    assert ev.media_type == "text/html"
    assert ev.charset == "utf-8"
    assert ev.error_message is None
    assert result.response is not None


@respx.mock
def test_head_with_evidence_wrapper_f016() -> None:
    respx.head("https://paulgraham.com/big.html").mock(
        return_value=httpx.Response(
            200,
            headers={"Content-Length": str(50_000_000), "Content-Type": "text/html"},
        )
    )
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
        result = head_with_evidence(
            client,
            "https://paulgraham.com/big.html",
            allowed_hosts=frozenset({"paulgraham.com"}),
            max_bytes=1024,
        )
    assert result.evidence.result_kind is ResultKind.FETCHED
    assert result.evidence.content_length_header == 50_000_000


@respx.mock
def test_get_large_body_capped() -> None:
    """GET with actual large body still fails closed against max_bytes."""
    respx.get("https://paulgraham.com/a.html").mock(
        return_value=httpx.Response(200, content=b"x" * 200)
    )
    with (
        httpx.Client(trust_env=False, follow_redirects=False) as client,
        pytest.raises(FeedError, match="over"),
    ):
        request_with_evidence(
            client,
            "GET",
            "https://paulgraham.com/a.html",
            allowed_hosts=frozenset({"paulgraham.com"}),
            max_bytes=50,
        )


@respx.mock
def test_get_content_length_oversize() -> None:
    """GET with declared Content-Length over max_bytes fails before full buffer."""
    respx.get("https://paulgraham.com/a.html").mock(
        return_value=httpx.Response(
            200,
            content=b"tiny",
            headers={"Content-Length": "99999"},
        )
    )
    with (
        httpx.Client(trust_env=False, follow_redirects=False) as client,
        pytest.raises(FeedError, match="over"),
    ):
        get_with_evidence(
            client,
            "https://paulgraham.com/a.html",
            allowed_hosts=frozenset({"paulgraham.com"}),
            max_bytes=50,
        )


@respx.mock
def test_get_happy_path_evidence_fields() -> None:
    """Evidence fields populated for happy-path GET."""
    body = b"<html>ok</html>"
    respx.get("https://paulgraham.com/a.html").mock(
        return_value=httpx.Response(
            200,
            content=body,
            headers={
                "Content-Type": "text/html; charset=utf-8",
                "ETag": '"v1"',
                "Last-Modified": "Tue, 02 Jan 2024 12:00:00 GMT",
                "Content-Length": str(len(body)),
            },
        )
    )
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
        result = request_with_evidence(
            client,
            "GET",
            "https://paulgraham.com/a.html",
            allowed_hosts=frozenset({"paulgraham.com"}),
            max_bytes=1024,
        )
    ev = result.evidence
    assert ev.method == "GET"
    assert ev.requested_url == "https://paulgraham.com/a.html"
    assert ev.final_url == "https://paulgraham.com/a.html"
    assert ev.status_code == 200
    assert ev.result_kind is ResultKind.FETCHED
    assert ev.media_type == "text/html"
    assert ev.charset == "utf-8"
    assert ev.etag == '"v1"'
    assert ev.last_modified == "Tue, 02 Jan 2024 12:00:00 GMT"
    assert ev.content_length_header == len(body)
    assert ev.raw_sha256 == hashlib.sha256(body).hexdigest()
    assert ev.bytes_received == len(body)
    assert ev.error_message is None
    assert result.body == body
    assert result.response is not None
    assert result.response.status_code == 200


@respx.mock
def test_not_modified_304() -> None:
    respx.get("https://paulgraham.com/a.html").mock(
        return_value=httpx.Response(304, headers={"ETag": '"v1"'})
    )
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
        result = request_with_evidence(
            client,
            "GET",
            "https://paulgraham.com/a.html",
            allowed_hosts=frozenset({"paulgraham.com"}),
            max_bytes=1024,
            headers={"If-None-Match": '"v1"'},
        )
    assert result.evidence.result_kind is ResultKind.NOT_MODIFIED
    assert result.evidence.status_code == 304
    assert result.evidence.etag == '"v1"'
    assert result.body == b""


@respx.mock
def test_redirect_chain_recorded() -> None:
    respx.get("https://paulgraham.com/a.html").mock(
        return_value=httpx.Response(302, headers={"Location": "https://paulgraham.com/b.html"})
    )
    respx.get("https://paulgraham.com/b.html").mock(
        return_value=httpx.Response(200, content=b"<html>final</html>")
    )
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
        result = request_with_evidence(
            client,
            "GET",
            "https://paulgraham.com/a.html",
            allowed_hosts=frozenset({"paulgraham.com"}),
            max_bytes=1024,
        )
    assert result.evidence.result_kind is ResultKind.FETCHED
    assert result.evidence.final_url == "https://paulgraham.com/b.html"
    assert result.evidence.redirect_urls == ("https://paulgraham.com/b.html",)
    assert b"final" in result.body


@respx.mock
def test_open_redirect_blocked() -> None:
    respx.get("https://paulgraham.com/a.html").mock(
        return_value=httpx.Response(302, headers={"Location": "https://evil.example/x"})
    )
    with (
        httpx.Client(trust_env=False, follow_redirects=False) as client,
        pytest.raises(FeedError, match="not allowed"),
    ):
        request_with_evidence(
            client,
            "GET",
            "https://paulgraham.com/a.html",
            allowed_hosts=frozenset({"paulgraham.com"}),
            max_bytes=1024,
        )


@respx.mock
def test_http_error_status_failed_evidence() -> None:
    respx.get("https://paulgraham.com/a.html").mock(
        return_value=httpx.Response(404, content=b"nope")
    )
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
        result = request_with_evidence(
            client,
            "GET",
            "https://paulgraham.com/a.html",
            allowed_hosts=frozenset({"paulgraham.com"}),
            max_bytes=1024,
        )
    assert result.evidence.result_kind is ResultKind.FAILED
    assert result.evidence.status_code == 404
    assert result.evidence.error_message == "HTTP 404"


def test_parse_content_type_charset_edge_cases() -> None:
    media, charset = parse_content_type('text/html; charset="UTF-8"')
    assert media == "text/html"
    assert charset == "utf-8"
    media2, charset2 = parse_content_type("text/html; boundary=x")
    assert media2 == "text/html"
    assert charset2 is None
    media3, charset3 = parse_content_type("text/html; charset=")
    assert media3 == "text/html"
    assert charset3 is None
    media4, charset4 = parse_content_type("text/html; CHARSET='ISO-8859-1'")
    assert media4 == "text/html"
    assert charset4 == "iso-8859-1"


@respx.mock
def test_transport_http_error_becomes_failed_evidence() -> None:
    """Transport-level httpx errors return FAILED evidence without raising."""
    respx.get("https://paulgraham.com/a.html").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
        result = request_with_evidence(
            client,
            "GET",
            "https://paulgraham.com/a.html",
            allowed_hosts=frozenset({"paulgraham.com"}),
            max_bytes=1024,
        )
    assert result.evidence.result_kind is ResultKind.FAILED
    assert result.evidence.status_code is None
    assert result.evidence.error_message is not None
    assert "connection refused" in result.evidence.error_message
    assert result.response is None
    assert result.body == b""


@respx.mock
def test_disallowed_host_raises() -> None:
    with (
        httpx.Client(trust_env=False, follow_redirects=False) as client,
        pytest.raises(FeedError, match="not allowed"),
    ):
        request_with_evidence(
            client,
            "GET",
            "https://evil.example/x",
            allowed_hosts=frozenset({"paulgraham.com"}),
            max_bytes=1024,
        )


@respx.mock
def test_non_https_non_loopback_raises() -> None:
    with (
        httpx.Client(trust_env=False, follow_redirects=False) as client,
        pytest.raises(FeedError, match="Need https"),
    ):
        request_with_evidence(
            client,
            "GET",
            "http://paulgraham.com/a.html",
            allowed_hosts=frozenset({"paulgraham.com"}),
            max_bytes=1024,
        )


@respx.mock
def test_www_host_normalized_to_paulgraham() -> None:
    respx.get("https://www.paulgraham.com/a.html").mock(
        return_value=httpx.Response(200, content=b"ok")
    )
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
        result = request_with_evidence(
            client,
            "GET",
            "https://www.paulgraham.com/a.html",
            allowed_hosts=frozenset({"paulgraham.com"}),
            max_bytes=1024,
        )
    assert result.evidence.result_kind is ResultKind.FETCHED


@respx.mock
def test_redirect_without_location_raises() -> None:
    respx.get("https://paulgraham.com/a.html").mock(return_value=httpx.Response(302, headers={}))
    with (
        httpx.Client(trust_env=False, follow_redirects=False) as client,
        pytest.raises(FeedError, match="Redirect without Location"),
    ):
        request_with_evidence(
            client,
            "GET",
            "https://paulgraham.com/a.html",
            allowed_hosts=frozenset({"paulgraham.com"}),
            max_bytes=1024,
        )


@respx.mock
def test_too_many_redirects_raises() -> None:
    respx.get("https://paulgraham.com/a.html").mock(
        return_value=httpx.Response(302, headers={"Location": "https://paulgraham.com/b.html"})
    )
    respx.get("https://paulgraham.com/b.html").mock(
        return_value=httpx.Response(302, headers={"Location": "https://paulgraham.com/a.html"})
    )
    with (
        httpx.Client(trust_env=False, follow_redirects=False) as client,
        pytest.raises(FeedError, match="Too many redirects"),
    ):
        request_with_evidence(
            client,
            "GET",
            "https://paulgraham.com/a.html",
            allowed_hosts=frozenset({"paulgraham.com"}),
            max_bytes=1024,
            max_hops=2,
        )


@respx.mock
def test_get_without_max_bytes_reads_full_body() -> None:
    body = b"<html>uncapped</html>"
    respx.get("https://paulgraham.com/a.html").mock(return_value=httpx.Response(200, content=body))
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
        result = request_with_evidence(
            client,
            "GET",
            "https://paulgraham.com/a.html",
            allowed_hosts=frozenset({"paulgraham.com"}),
            max_bytes=None,
        )
    assert result.body == body
    assert result.evidence.bytes_received == len(body)


@respx.mock
def test_relative_redirect_resolved() -> None:
    respx.get("https://paulgraham.com/dir/a.html").mock(
        return_value=httpx.Response(301, headers={"Location": "../b.html"})
    )
    respx.get("https://paulgraham.com/b.html").mock(
        return_value=httpx.Response(200, content=b"done")
    )
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
        result = request_with_evidence(
            client,
            "GET",
            "https://paulgraham.com/dir/a.html",
            allowed_hosts=frozenset({"paulgraham.com"}),
            max_bytes=1024,
        )
    assert result.evidence.final_url == "https://paulgraham.com/b.html"
    assert result.body == b"done"


@respx.mock
def test_loopback_allowed_for_http() -> None:
    respx.get("http://127.0.0.1:9/x").mock(return_value=httpx.Response(200, content=b"local"))
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
        result = request_with_evidence(
            client,
            "GET",
            "http://127.0.0.1:9/x",
            allowed_hosts=frozenset({"paulgraham.com"}),
            max_bytes=1024,
            allow_loopback=True,
        )
    assert result.evidence.result_kind is ResultKind.FETCHED
    assert result.body == b"local"


@respx.mock
def test_loopback_blocked_when_disallowed() -> None:
    with (
        httpx.Client(trust_env=False, follow_redirects=False) as client,
        pytest.raises(FeedError, match="not allowed"),
    ):
        request_with_evidence(
            client,
            "GET",
            "http://127.0.0.1/x",
            allowed_hosts=frozenset({"paulgraham.com"}),
            max_bytes=1024,
            allow_loopback=False,
        )


@respx.mock
def test_invalid_content_length_header_ignored() -> None:
    body = b"abc"
    respx.get("https://paulgraham.com/a.html").mock(
        return_value=httpx.Response(
            200,
            content=body,
            headers={"Content-Length": "not-a-number"},
        )
    )
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
        result = request_with_evidence(
            client,
            "GET",
            "https://paulgraham.com/a.html",
            allowed_hosts=frozenset({"paulgraham.com"}),
            max_bytes=1024,
        )
    assert result.evidence.content_length_header is None
    assert result.body == body


@respx.mock
def test_server_error_status_failed_evidence() -> None:
    respx.get("https://paulgraham.com/a.html").mock(
        return_value=httpx.Response(503, content=b"busy")
    )
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
        result = get_with_evidence(
            client,
            "https://paulgraham.com/a.html",
            allowed_hosts=frozenset({"paulgraham.com"}),
            max_bytes=1024,
        )
    assert result.evidence.result_kind is ResultKind.FAILED
    assert result.evidence.status_code == 503
    assert result.evidence.error_message == "HTTP 503"
