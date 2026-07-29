"""Integration: fetch_html against a local HTTP server."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from paul_graham_essay_feeds.http import fetch_html
from paul_graham_essay_feeds.models import FeedError
from tests.html_samples import synthetic_index_html

pytestmark = pytest.mark.integration


def _handler_factory(*, body: bytes, status: int = 200) -> type[BaseHTTPRequestHandler]:
    """Return a request-handler class closed over per-server body/status (no class mutables)."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if status == 200:
                self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler


@pytest.fixture
def http_server():
    handler = _handler_factory(body=synthetic_index_html().encode("utf-8"), status=200)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    yield f"http://{host}:{port}/articles.html"
    server.shutdown()


def test_fetch_html_from_local_server(http_server: str) -> None:
    html = fetch_html(http_server, timeout=5.0, retries=0)
    assert "Essay 0" in html


def test_fetch_html_http_error() -> None:
    handler = _handler_factory(body=b"", status=500)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    url = f"http://{host}:{port}/boom"
    try:
        with pytest.raises(FeedError, match="HTTP 500"):
            fetch_html(url, timeout=2.0, retries=0)
    finally:
        server.shutdown()
