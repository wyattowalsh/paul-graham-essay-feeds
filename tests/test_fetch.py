"""Local HTTP server tests for conditional fetch (stdlib urllib)."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from paul_graham_essay_feeds.domain import FeedError
from paul_graham_essay_feeds.fetch import (
    assert_source_host,
    assert_source_transport,
    fetch_source,
)


def test_assert_source_host_rejects_unexpected() -> None:
    with pytest.raises(FeedError, match="not allowed"):
        assert_source_host(
            "https://evil.example/articles.html",
            allowed=frozenset({"paulgraham.com"}),
        )


def test_assert_source_host_normalizes_www() -> None:
    assert_source_host(
        "https://www.paulgraham.com/articles.html",
        allowed=frozenset({"paulgraham.com"}),
    )


def test_assert_source_transport_rejects_http_non_loopback() -> None:
    with pytest.raises(FeedError, match="must be https"):
        assert_source_transport(
            "http://paulgraham.com/articles.html",
            allowed=frozenset({"paulgraham.com"}),
        )


def test_assert_source_transport_allows_https() -> None:
    assert_source_transport(
        "https://paulgraham.com/articles.html",
        allowed=frozenset({"paulgraham.com"}),
    )


def test_assert_source_transport_allows_loopback_http() -> None:
    assert_source_transport(
        "http://127.0.0.1:8080/",
        allowed=frozenset({"127.0.0.1"}),
    )


def test_fetch_and_conditional_304() -> None:
    body = b"<html><body>ok</body></html>"

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.headers.get("If-None-Match") == '"fixture-etag"':
                self.send_response(304)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("ETag", '"fixture-etag"')
            self.send_header("Last-Modified", "Fri, 11 Jul 2026 00:00:00 GMT")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/"
        first = fetch_source(
            url,
            timeout=5,
            retries=1,
            max_bytes=1024 * 1024,
            state={},
            conditional=False,
            # Local test server is 127.0.0.1 — allow for unit test only.
            source_allowed_hosts=frozenset({"127.0.0.1"}),
        )
        assert first.status == 200
        assert first.body == body
        assert first.etag == '"fixture-etag"'

        second = fetch_source(
            url,
            timeout=5,
            retries=1,
            max_bytes=1024 * 1024,
            state={"etag": '"fixture-etag"', "last_modified": first.last_modified},
            conditional=True,
            source_allowed_hosts=frozenset({"127.0.0.1"}),
        )
        assert second.not_modified
        assert second.status == 304
        assert second.body is None
    finally:
        server.shutdown()
