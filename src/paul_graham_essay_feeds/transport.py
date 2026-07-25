"""HTTP transport evidence and hop-safe requests (ADR-004 / F-016).

Clean client API intended to eventually replace direct ``hop_safe_*`` usage
from :mod:`paul_graham_essay_feeds.fetch`. Hop allowlist semantics match
``fetch`` (HTTPS + optional loopback HTTP; ``www.paulgraham.com`` normalized).

**F-016:** HEAD must not treat representation ``Content-Length`` as a
downloaded-body budget. Body size caps apply only when a GET body is read.
"""

from __future__ import annotations

import contextlib
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from urllib.parse import urlsplit

import httpx
from loguru import logger

from paul_graham_essay_feeds.model import FeedError

_LOOPBACK: Final = frozenset({"127.0.0.1", "localhost", "::1"})
_DEFAULT_MAX_HOPS: Final = 6

# Soft HTML / text media types (advisory only — never hard-fail here).
SOFT_HTML_MEDIA_TYPES: Final[frozenset[str]] = frozenset(
    {
        "text/html",
        "application/xhtml+xml",
        "application/xml",
        "text/xml",
        "text/plain",
    }
)


class ResultKind(StrEnum):
    """Outcome classification for a transport attempt."""

    FETCHED = "fetched"
    NOT_MODIFIED = "not_modified"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class FetchEvidence:
    """Typed transport evidence for one request (ADR-004).

    Field set follows the W1-06 contract; additional redirect/retry fields may
    be layered on later without breaking these names.
    """

    method: str
    requested_url: str
    final_url: str
    status_code: int | None
    result_kind: ResultKind
    media_type: str | None = None
    charset: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    content_length_header: int | None = None
    raw_sha256: str | None = None
    bytes_received: int = 0
    error_message: str | None = None
    redirect_urls: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TransportResult:
    """Outcome of :func:`request_with_evidence`.

    ``response`` is a fully buffered :class:`httpx.Response` when the exchange
    completed (including non-2xx). ``body`` is the final entity bytes for
    methods that transfer a body (empty for HEAD / 304).
    """

    evidence: FetchEvidence
    response: httpx.Response | None = None
    body: bytes = b""


def parse_content_type(header: str | None) -> tuple[str | None, str | None]:
    """Parse ``Content-Type`` into ``(media_type, charset)``.

    Soft helper: invalid / missing headers yield ``(None, None)`` rather than
    raising. Charset is lowercased when present.
    """
    if not header or not header.strip():
        return None, None
    parts = [p.strip() for p in header.split(";")]
    media_type = parts[0].lower() if parts and parts[0] else None
    if not media_type or "/" not in media_type:
        return None, None
    charset: str | None = None
    for param in parts[1:]:
        if "=" not in param:
            continue
        key, _, value = param.partition("=")
        if key.strip().lower() == "charset":
            stripped = value.strip().strip("\"'").lower()
            charset = stripped or None
            break
    return media_type, charset


def media_type_is_soft_html(media_type: str | None) -> bool:
    """Return True when *media_type* is a soft-accepted HTML/text type.

    Advisory only — callers may log or deprioritize unexpected types without
    hard-breaking the pipeline (ADR-004 soft validation).
    """
    if media_type is None:
        return False
    base = media_type.split(";", 1)[0].strip().lower()
    return base in SOFT_HTML_MEDIA_TYPES


def _normalize_host(host: str) -> str:
    host = host.lower()
    return "paulgraham.com" if host == "www.paulgraham.com" else host


def _assert_hop_allowed(
    url: str,
    allowed_hosts: frozenset[str] | set[str],
    *,
    allow_loopback: bool,
) -> None:
    """Raise FeedError unless ``url`` scheme/host is permitted for this hop."""
    parts = urlsplit(url)
    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").lower()
    if host in _LOOPBACK:
        if not allow_loopback:
            raise FeedError(f"Host not allowed: {host!r}")
        if not (scheme == "https" or scheme == "http"):
            raise FeedError(f"Need https (or http loopback): {url!r}")
        return
    if scheme != "https":
        raise FeedError(f"Need https (or http loopback): {url!r}")
    allowed = {_normalize_host(h) for h in allowed_hosts}
    if _normalize_host(host) not in allowed:
        raise FeedError(f"Host not allowed: {host!r}")


def _parse_content_length_value(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _parse_content_length(response: httpx.Response) -> int | None:
    return _parse_content_length_value(response.headers.get("content-length"))


def _read_body_capped(response: httpx.Response, *, max_bytes: int) -> bytes:
    """Read response body with a hard size cap (Content-Length + stream).

    Raises :class:`FeedError` if the declared or actual body exceeds ``max_bytes``.
    Only for methods that transfer an entity body (GET).
    """
    declared = _parse_content_length(response)
    if declared is not None and declared > max_bytes:
        response.close()
        raise FeedError(f"Response over {max_bytes} bytes")
    buf = bytearray()
    try:
        for chunk in response.iter_bytes():
            buf.extend(chunk)
            if len(buf) > max_bytes:
                raise FeedError(f"Response over {max_bytes} bytes")
    finally:
        response.close()
    return bytes(buf)


def _rebuild_response(
    response: httpx.Response,
    *,
    body: bytes,
) -> httpx.Response:
    """Buffer *body* into a new Response (drop encoding headers after stream)."""
    headers = httpx.Headers(response.headers)
    headers.pop("Content-Encoding", None)
    headers.pop("Content-Length", None)
    return httpx.Response(
        status_code=response.status_code,
        headers=headers,
        content=body,
        request=response.request,
        extensions=response.extensions,
        history=response.history,
    )


def _header_snapshot(response: httpx.Response) -> dict[str, str | None]:
    """Capture evidence-relevant headers before stream rebuild drops them."""
    return {
        "content-type": response.headers.get("content-type"),
        "content-length": response.headers.get("content-length"),
        "etag": response.headers.get("etag"),
        "last-modified": response.headers.get("last-modified"),
    }


def _failed_evidence(
    *,
    method: str,
    requested_url: str,
    final_url: str,
    status_code: int | None,
    error_message: str,
    redirect_urls: tuple[str, ...] = (),
    media_type: str | None = None,
    charset: str | None = None,
    etag: str | None = None,
    last_modified: str | None = None,
    content_length_header: int | None = None,
    bytes_received: int = 0,
    raw_sha256: str | None = None,
) -> FetchEvidence:
    return FetchEvidence(
        method=method.upper(),
        requested_url=requested_url,
        final_url=final_url,
        status_code=status_code,
        result_kind=ResultKind.FAILED,
        media_type=media_type,
        charset=charset,
        etag=etag,
        last_modified=last_modified,
        content_length_header=content_length_header,
        raw_sha256=raw_sha256,
        bytes_received=bytes_received,
        error_message=error_message,
        redirect_urls=redirect_urls,
    )


def _build_evidence(
    *,
    method: str,
    requested_url: str,
    final_url: str,
    status_code: int,
    body: bytes,
    header_snap: Mapping[str, str | None],
    redirect_urls: tuple[str, ...],
) -> FetchEvidence:
    media_type, charset = parse_content_type(header_snap.get("content-type"))
    cl = _parse_content_length_value(header_snap.get("content-length"))
    bytes_received = len(body)
    raw_sha256 = hashlib.sha256(body).hexdigest() if body else None
    if status_code == 304:
        kind = ResultKind.NOT_MODIFIED
        error_message = None
    elif 200 <= status_code < 300:
        kind = ResultKind.FETCHED
        error_message = None
    else:
        kind = ResultKind.FAILED
        error_message = f"HTTP {status_code}"
    return FetchEvidence(
        method=method.upper(),
        requested_url=requested_url,
        final_url=final_url,
        status_code=status_code,
        result_kind=kind,
        media_type=media_type,
        charset=charset,
        etag=header_snap.get("etag"),
        last_modified=header_snap.get("last-modified"),
        content_length_header=cl,
        raw_sha256=raw_sha256,
        bytes_received=bytes_received,
        error_message=error_message,
        redirect_urls=redirect_urls,
    )


def request_with_evidence(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    allowed_hosts: frozenset[str] | set[str],
    max_bytes: int | None = None,
    max_hops: int = _DEFAULT_MAX_HOPS,
    allow_loopback: bool | None = None,
    headers: Mapping[str, str] | None = None,
) -> TransportResult:
    """Issue *method* with hop-validated redirects and return typed evidence.

    Hop-safe semantics (aligned with ``fetch.hop_safe_request``):

    - ``follow_redirects=False`` on every ``send`` (SSRF-safe)
    - each hop re-checked against *allowed_hosts* (+ start-bound loopback)
    - redirect responses closed without reading the body

    Body budget (**F-016**):

    - **HEAD:** never fail solely because ``Content-Length`` > *max_bytes*;
      no entity body is budgeted.
    - **GET** (and other body-bearing methods): when *max_bytes* is set, enforce
      via ``Content-Length`` (when present) and a streaming hard-stop.

    Raises :class:`FeedError` for policy violations (disallowed host, too many
    redirects, oversize GET body). Transport-level exceptions become
    ``result_kind=failed`` evidence (no raise).
    """
    if allow_loopback is None:
        start_host = (urlsplit(url).hostname or "").lower()
        allow_loopback = start_host in _LOOPBACK

    method_u = method.upper()
    # Body size budget applies only when we actually read a GET (entity) body.
    apply_body_budget = max_bytes is not None and method_u != "HEAD"

    current = url
    redirect_chain: list[str] = []
    response: httpx.Response | None = None
    extra_headers = dict(headers) if headers else None
    header_snap: dict[str, str | None] = {}
    final_url = url
    status_code = 0

    try:
        for _hop in range(max_hops):
            _assert_hop_allowed(current, allowed_hosts, allow_loopback=allow_loopback)
            logger.debug("{} {}", method_u, current)
            req = client.build_request(method_u, current, headers=extra_headers)
            # Force no auto-follow even if Client was constructed with
            # follow_redirects=True — otherwise Location targets are fetched
            # before the next-hop allowlist check (SSRF).
            response = client.send(req, stream=True, follow_redirects=False)
            try:
                # Explicit hop redirects only (exclude 304 Not Modified).
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    response.close()
                    response = None
                    if not location:
                        raise FeedError(f"Redirect without Location from {current}")
                    next_url = str(httpx.URL(current).join(location))
                    redirect_chain.append(next_url)
                    current = next_url
                    # Conditional headers apply only to the first hop.
                    extra_headers = None
                    continue

                # Snapshot headers before rebuild drops Content-Length / encoding.
                header_snap = _header_snapshot(response)
                final_url = str(response.url)
                status_code = response.status_code

                # Final (non-redirect) response.
                if method_u == "HEAD":
                    # F-016: do not apply Content-Length as a body budget.
                    # Close without draining; HEAD has no entity body to budget.
                    response.close()
                    body = b""
                    buffered = _rebuild_response(response, body=body)
                    response = buffered
                elif apply_body_budget:
                    assert max_bytes is not None
                    body = _read_body_capped(response, max_bytes=max_bytes)
                    buffered = _rebuild_response(response, body=body)
                    response = buffered
                else:
                    try:
                        body = response.read()
                    finally:
                        response.close()
                    buffered = _rebuild_response(response, body=body)
                    response = buffered
                break
            except Exception:
                if response is not None:
                    with contextlib.suppress(Exception):
                        response.close()
                raise
        else:
            raise FeedError(f"Too many redirects for {url}")

        assert response is not None
        _assert_hop_allowed(str(response.url), allowed_hosts, allow_loopback=allow_loopback)

        body = response.content
        if apply_body_budget and max_bytes is not None and len(body) > max_bytes:
            raise FeedError(f"Response over {max_bytes} bytes")

        evidence = _build_evidence(
            method=method_u,
            requested_url=url,
            final_url=final_url,
            status_code=status_code,
            body=body,
            header_snap=header_snap,
            redirect_urls=tuple(redirect_chain),
        )
        return TransportResult(evidence=evidence, response=response, body=body)

    except httpx.HTTPError as exc:
        return TransportResult(
            evidence=_failed_evidence(
                method=method_u,
                requested_url=url,
                final_url=current,
                status_code=None,
                error_message=str(exc),
                redirect_urls=tuple(redirect_chain),
            ),
            response=None,
            body=b"",
        )


def get_with_evidence(
    client: httpx.Client,
    url: str,
    *,
    allowed_hosts: frozenset[str] | set[str],
    max_bytes: int,
    max_hops: int = _DEFAULT_MAX_HOPS,
    allow_loopback: bool | None = None,
    headers: Mapping[str, str] | None = None,
) -> TransportResult:
    """GET with hop-validated redirects and body size budget."""
    return request_with_evidence(
        client,
        "GET",
        url,
        allowed_hosts=allowed_hosts,
        max_bytes=max_bytes,
        max_hops=max_hops,
        allow_loopback=allow_loopback,
        headers=headers,
    )


def head_with_evidence(
    client: httpx.Client,
    url: str,
    *,
    allowed_hosts: frozenset[str] | set[str],
    max_bytes: int | None = None,
    max_hops: int = _DEFAULT_MAX_HOPS,
    allow_loopback: bool | None = None,
    headers: Mapping[str, str] | None = None,
) -> TransportResult:
    """HEAD with hop-validated redirects; *max_bytes* never applied to Content-Length."""
    return request_with_evidence(
        client,
        "HEAD",
        url,
        allowed_hosts=allowed_hosts,
        max_bytes=max_bytes,
        max_hops=max_hops,
        allow_loopback=allow_loopback,
        headers=headers,
    )
