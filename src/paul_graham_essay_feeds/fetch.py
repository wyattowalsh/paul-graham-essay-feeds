"""Conditional HTTP fetch for the official essays index.

**Transport decision (researched):** use the Python **standard library** only —

* ``urllib.request`` for HTTPS GET
* ``urllib.error`` for HTTP errors

Do **not** use ``httpx``, ``requests``, or ``trafilatura``:

* Project constraint: zero runtime dependencies by default.
* ``trafilatura`` extracts article body text; this pipeline needs the full HTML
  index structure (essay-row markers), not cleaned article text.
* Baseline behavior (ETag/Last-Modified, identity encoding, 5 MiB cap, bounded
  retries) is already proven with stdlib and must be preserved.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from paul_graham_essay_feeds import __version__
from paul_graham_essay_feeds.domain import (
    CHANNEL_URL,
    RETRYABLE_HTTP_CODES,
    FeedError,
    FetchResult,
)

__all__ = [
    "assert_source_host",
    "assert_source_transport",
    "decode_source",
    "fetch_source",
    "normalize_source_host",
    "read_limited",
]

DEFAULT_SOURCE_ALLOWED_HOSTS: frozenset[str] = frozenset({"paulgraham.com"})
LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "localhost", "::1"})


def normalize_source_host(host: str) -> str:
    """Normalize a hostname for source-allowlist comparison."""
    value = host.lower().rstrip(".")
    if value == "www.paulgraham.com":
        return "paulgraham.com"
    return value


def assert_source_host(url: str, *, allowed: frozenset[str]) -> None:
    """Raise :class:`FeedError` when ``url`` host is not in ``allowed``.

    Used after redirects so a compromised or misconfigured source cannot feed
    HTML from an unexpected host into extraction.
    """
    host = urlsplit(url).hostname
    if not host:
        raise FeedError(f"Source URL has no host: {url!r}")
    normalized = normalize_source_host(host)
    allowed_norm = frozenset(normalize_source_host(h) for h in allowed)
    if normalized not in allowed_norm:
        raise FeedError(
            f"Source final URL host not allowed: {normalized!r} (from {url!r}). "
            f"Allowed: {sorted(allowed_norm)}"
        )


def assert_source_transport(url: str, *, allowed: frozenset[str]) -> None:
    """Require HTTPS (or HTTP only for loopback test hosts) then host allowlist.

    Production index fetches must use HTTPS. Unit tests may use
    ``http://127.0.0.1`` / ``localhost`` / ``::1``.
    """
    parts = urlsplit(url)
    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").lower()
    if not (scheme == "https" or (scheme == "http" and host in LOOPBACK_HOSTS)):
        raise FeedError(f"Source URL must be https (or http loopback for tests): {url!r}")
    assert_source_host(url, allowed=allowed)


def read_limited(response: Any, *, max_bytes: int) -> bytes:
    """Read a response body with a hard size cap."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(min(64 * 1024, max_bytes + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > max_bytes:
            raise FeedError(f"Source response exceeded maximum size of {max_bytes} bytes.")
    return b"".join(chunks)


def fetch_source(
    url: str,
    *,
    timeout: float,
    retries: int,
    max_bytes: int,
    state: Mapping[str, Any],
    conditional: bool,
    source_allowed_hosts: frozenset[str] | None = None,
) -> FetchResult:
    """Fetch ``url`` with optional conditional headers and bounded retries.

    Parameters
    ----------
    url :
        Absolute source URL (HTTPS, or HTTP loopback for tests).
    timeout :
        Socket timeout in seconds.
    retries :
        Number of additional attempts after the first failure.
    max_bytes :
        Maximum accepted response body size.
    state :
        Prior transport state (``etag``, ``last_modified``).
    conditional :
        When True, send If-None-Match / If-Modified-Since when available.
    source_allowed_hosts :
        Hosts permitted for the **final** URL after redirects (defaults to
        ``paulgraham.com``). Distinct from item-link host allowlists.
    """
    allowed = (
        source_allowed_hosts if source_allowed_hosts is not None else DEFAULT_SOURCE_ALLOWED_HOSTS
    )
    assert_source_transport(url, allowed=allowed)

    headers = {
        "User-Agent": f"pg-essay-feeds/{__version__} (+{CHANNEL_URL})",
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
        "Accept-Encoding": "identity",
        "Cache-Control": "no-cache",
    }
    if conditional:
        etag = state.get("etag")
        last_modified = state.get("last_modified")
        if isinstance(etag, str) and etag:
            headers["If-None-Match"] = etag
        if isinstance(last_modified, str) and last_modified:
            headers["If-Modified-Since"] = last_modified

    for attempt in range(retries + 1):
        request = Request(url, headers=headers)
        try:
            with urlopen(request, timeout=timeout) as response:
                body = read_limited(response, max_bytes=max_bytes)
                content_type = response.headers.get_content_type()
                if content_type not in {
                    "text/html",
                    "application/xhtml+xml",
                    "text/plain",
                }:
                    raise FeedError(f"Unexpected source content type: {content_type!r}.")
                final_url = response.geturl()
                assert_source_transport(final_url, allowed=allowed)
                return FetchResult(
                    body=body,
                    final_url=final_url,
                    etag=response.headers.get("ETag"),
                    last_modified=response.headers.get("Last-Modified"),
                    status=getattr(response, "status", 200),
                    not_modified=False,
                )
        except HTTPError as exc:
            if exc.code == 304:
                assert_source_transport(url, allowed=allowed)
                return FetchResult(
                    body=None,
                    final_url=url,
                    etag=state.get("etag") if isinstance(state.get("etag"), str) else None,
                    last_modified=(
                        state.get("last_modified")
                        if isinstance(state.get("last_modified"), str)
                        else None
                    ),
                    status=304,
                    not_modified=True,
                )
            if exc.code not in RETRYABLE_HTTP_CODES or attempt >= retries:
                raise FeedError(f"HTTP {exc.code} while fetching {url}.") from exc
        except (URLError, TimeoutError, OSError) as exc:
            if attempt >= retries:
                raise FeedError(f"Unable to fetch {url}: {exc}") from exc

        time.sleep(min(2**attempt, 8))

    raise AssertionError("retry loop exhausted unexpectedly")


def decode_source(body: bytes) -> str:
    """Decode source HTML bytes to text (UTF-8 with Latin-1 fallback)."""
    if body.startswith(b"\xef\xbb\xbf"):
        return body.decode("utf-8-sig")
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("latin-1")
