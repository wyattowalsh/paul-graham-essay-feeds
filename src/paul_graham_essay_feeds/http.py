"""Unified HTTP facade: hop-safe transport, decode, retry, index fetch (AD-004).

Owns transport evidence, HTML decoding, retry policy, and :func:`fetch_index` /
:func:`fetch_html`. Leaf modules were folded into this module.
"""

from __future__ import annotations

import contextlib
import hashlib
import random
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from enum import StrEnum
from typing import Final
from urllib.parse import urlsplit

import httpx
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field
from tenacity import (
    RetryCallState,
    RetryError,
    Retrying,
    retry_if_exception,
    stop_after_attempt,
)

from paul_graham_essay_feeds.models import (
    MAX_BYTES,
    SOURCE_URL,
    FeedError,
    NetworkSourceError,
    user_agent,
)

# --- Decoding (absorbed) ---

# Windows-1252 is preferred over ISO-8859-1 for legacy HTML punctuation (C1 range).
_META_CHARSET = re.compile(
    rb"(?is)<meta[^>]+charset\s*=\s*[\"']?\s*([a-zA-Z0-9_\-]+)",
)
_META_CONTENT_TYPE = re.compile(
    rb"(?is)<meta[^>]+http-equiv\s*=\s*[\"']?content-type[\"']?[^>]+content\s*=\s*[\"'][^\"']*charset\s*=\s*([a-zA-Z0-9_\-]+)",
)
_ALLOWED_ENCODINGS = frozenset(
    {
        "utf-8",
        "utf8",
        "windows-1252",
        "cp1252",
        "iso-8859-1",
        "latin-1",
        "latin1",
    }
)


class EncodingSource(StrEnum):
    BOM = "bom"
    TRANSPORT = "transport"
    META = "meta"
    UTF8_STRICT = "utf8_strict"
    WINDOWS_1252_FALLBACK = "windows_1252_fallback"


@dataclass(frozen=True, slots=True)
class DecodedDocument:
    """Decoded HTML text plus encoding selection evidence."""

    text: str
    encoding: str
    source: EncodingSource
    had_bom: bool = False
    replacement_count: int = 0


def _normalize_encoding_label(label: str) -> str | None:
    key = label.strip().lower().replace("_", "-")
    if key not in _ALLOWED_ENCODINGS and key not in {"utf-8", "utf8"}:
        # Allow common aliases only from the allowlist for safety.
        return None
    if key in {"utf8", "utf-8"}:
        return "utf-8"
    if key in {"cp1252", "windows-1252"}:
        return "windows-1252"
    if key in {"latin-1", "latin1", "iso-8859-1"}:
        return "windows-1252"  # treat latin-1 declaration as cp1252 for HTML
    return key


def _prescan_meta_charset(raw: bytes, limit: int = 4096) -> str | None:
    head = raw[:limit]
    for pattern in (_META_CHARSET, _META_CONTENT_TYPE):
        match = pattern.search(head)
        if match:
            return _normalize_encoding_label(match.group(1).decode("ascii", errors="ignore"))
    return None


def decode_html_document(
    body: bytes,
    *,
    transport_charset: str | None = None,
) -> DecodedDocument:
    """Decode HTML bytes using the ADR-004 priority chain.

    1. BOM
    2. Valid transport charset
    3. Early in-document meta charset
    4. Strict UTF-8
    5. Windows-1252 fallback
    """
    had_bom = body.startswith(b"\xef\xbb\xbf")
    if had_bom:
        text = body.decode("utf-8-sig")
        return DecodedDocument(
            text=text,
            encoding="utf-8",
            source=EncodingSource.BOM,
            had_bom=True,
            replacement_count=text.count("\ufffd"),
        )

    if transport_charset:
        enc = _normalize_encoding_label(transport_charset)
        if enc:
            try:
                text = body.decode(enc)
                return DecodedDocument(
                    text=text,
                    encoding=enc,
                    source=EncodingSource.TRANSPORT,
                    replacement_count=text.count("\ufffd"),
                )
            except LookupError:
                pass
            except UnicodeDecodeError:
                pass

    meta = _prescan_meta_charset(body)
    if meta:
        try:
            text = body.decode(meta)
            return DecodedDocument(
                text=text,
                encoding=meta,
                source=EncodingSource.META,
                replacement_count=text.count("\ufffd"),
            )
        except (LookupError, UnicodeDecodeError):
            pass

    try:
        text = body.decode("utf-8")
        return DecodedDocument(
            text=text,
            encoding="utf-8",
            source=EncodingSource.UTF8_STRICT,
            replacement_count=0,
        )
    except UnicodeDecodeError:
        text = body.decode("windows-1252", errors="replace")
        return DecodedDocument(
            text=text,
            encoding="windows-1252",
            source=EncodingSource.WINDOWS_1252_FALLBACK,
            replacement_count=text.count("\ufffd"),
        )


def decode_html(body: bytes, *, transport_charset: str | None = None) -> str:
    """Back-compat helper returning text only."""
    return decode_html_document(body, transport_charset=transport_charset).text


# --- Retry policy (absorbed) ---

# Status codes worth retrying for idempotent GETs (aligned with fetch policy).
RETRYABLE_HTTP_STATUS: Final[frozenset[int]] = frozenset({408, 425, 429, 500, 502, 503, 504})
# Client errors that are retryable (subset of 4xx).
_RETRYABLE_CLIENT_STATUS: Final[frozenset[int]] = frozenset({408, 425, 429})

# Bound how long we honor a server-supplied Retry-After (seconds).
DEFAULT_MAX_RETRY_AFTER: Final[float] = 120.0

# Default full-jitter window (seconds) — matches prior fetch-scale backoff.
DEFAULT_JITTER_INITIAL: Final[float] = 0.4
DEFAULT_JITTER_MAX: Final[float] = 8.0
DEFAULT_JITTER_EXP_BASE: Final[float] = 2.0


def parse_retry_after(
    value: str | None,
    *,
    now: datetime | None = None,
    max_wait: float = DEFAULT_MAX_RETRY_AFTER,
) -> float | None:
    """Parse a ``Retry-After`` header into a non-negative wait in seconds.

    Accepts RFC 7231 forms:

    * **delta-seconds** — non-negative integer (digits only after strip)
    * **HTTP-date** — IMF-fixdate / RFC 5322 date (via ``parsedate_to_datetime``)

    Returns ``None`` when the header is missing, empty, or unparseable.
    Past HTTP-dates yield ``0.0``. Results are capped at ``max_wait`` (default 120s).
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if max_wait < 0:
        max_wait = 0.0

    # Prefer delta-seconds when the whole token is a non-negative integer.
    delta = _parse_delta_seconds(text)
    if delta is not None:
        return min(float(delta), max_wait)

    when = _parse_http_date(text)
    if when is None:
        return None
    current = _aware_utc(now) if now is not None else datetime.now(UTC)
    wait = (when - current).total_seconds()
    if wait < 0:
        return 0.0
    return min(wait, max_wait)


def _parse_delta_seconds(text: str) -> int | None:
    """Return integer seconds if ``text`` is a pure integer token; else None."""
    candidate = text
    if candidate.startswith("+"):
        candidate = candidate[1:]
    if not candidate.isdigit():
        return None
    return int(candidate)


def _parse_http_date(text: str) -> datetime | None:
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if when.tzinfo is None:
        # HTTP-date is defined in GMT; treat naive as UTC.
        return when.replace(tzinfo=UTC)
    return when.astimezone(UTC)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def full_jitter_seconds(
    attempt_number: int,
    *,
    initial: float = DEFAULT_JITTER_INITIAL,
    max_wait: float = DEFAULT_JITTER_MAX,
    exp_base: float = DEFAULT_JITTER_EXP_BASE,
    rng: random.Random | None = None,
) -> float:
    """True **full jitter** delay for a 1-based attempt number.

    Window high bound::

        high = min(max_wait, initial * exp_base ** (attempt_number - 1))

    Return value is drawn uniformly from ``[0, high]`` (inclusive bounds as
    provided by ``random.Random.uniform``).

    This is the AWS "Full Jitter" strategy and matches tenacity's
    ``wait_random_exponential`` semantics — **not** additive
    ``wait_exponential_jitter`` (``exp + U(0, j)``).

    ``attempt_number`` is 1-based (first failure → 1), matching tenacity's
    ``RetryCallState.attempt_number`` when computing the next sleep.
    """
    if attempt_number < 1:
        attempt_number = 1
    if initial < 0:
        initial = 0.0
    if max_wait < 0:
        max_wait = 0.0
    if exp_base < 1:
        exp_base = 1.0
    try:
        high = initial * (exp_base ** (attempt_number - 1))
    except OverflowError:
        high = max_wait
    high = max(0.0, min(float(high), max_wait))
    if high == 0.0:
        return 0.0
    choose = rng.uniform if rng is not None else random.uniform
    return float(choose(0.0, high))


class wait_full_jitter:
    """Tenacity-compatible wait callable implementing true full jitter.

    Use with ``tenacity.Retrying(wait=wait_full_jitter(...), ...)``.
    Samples ``U(0, min(max, initial * exp_base ** (attempt - 1)))``.
    """

    def __init__(
        self,
        *,
        initial: float = DEFAULT_JITTER_INITIAL,
        max: float = DEFAULT_JITTER_MAX,
        exp_base: float = DEFAULT_JITTER_EXP_BASE,
        rng: random.Random | None = None,
    ) -> None:
        self.initial = initial
        self.max = max
        self.exp_base = exp_base
        self.rng = rng

    def __call__(self, retry_state: RetryCallState) -> float:
        return full_jitter_seconds(
            retry_state.attempt_number,
            initial=self.initial,
            max_wait=self.max,
            exp_base=self.exp_base,
            rng=self.rng,
        )


class wait_retry_after_or_jitter:
    """Wait the greater of full-jitter backoff and a bounded ``Retry-After``.

    Honors both delta-seconds and HTTP-date ``Retry-After`` values (via
    :func:`parse_retry_after`), capped at ``max_retry_after`` (default 120s).
    """

    def __init__(
        self,
        *,
        initial: float = DEFAULT_JITTER_INITIAL,
        max: float = DEFAULT_JITTER_MAX,
        exp_base: float = DEFAULT_JITTER_EXP_BASE,
        max_retry_after: float = DEFAULT_MAX_RETRY_AFTER,
        rng: random.Random | None = None,
    ) -> None:
        self._jitter = wait_full_jitter(initial=initial, max=max, exp_base=exp_base, rng=rng)
        self.max_retry_after = max_retry_after

    def __call__(self, retry_state: RetryCallState) -> float:
        jitter = float(self._jitter(retry_state))
        retry_after = 0.0
        if retry_state.outcome is not None and retry_state.outcome.failed:
            exc = retry_state.outcome.exception()
            if isinstance(exc, httpx.HTTPStatusError):
                header = exc.response.headers.get("Retry-After")
                parsed = parse_retry_after(header, max_wait=self.max_retry_after)
                if parsed is not None:
                    retry_after = float(parsed)
        return max(jitter, retry_after)


class TimeoutConfig(BaseModel):
    """Granular HTTP timeouts for httpx (connect / read / write / pool)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    connect: float = Field(
        default=5.0,
        gt=0,
        description="Seconds to establish a TCP/TLS connection.",
    )
    read: float = Field(
        default=30.0,
        gt=0,
        description="Seconds to wait for bytes on an established connection.",
    )
    write: float = Field(
        default=30.0,
        gt=0,
        description="Seconds to send request body / headers.",
    )
    pool: float = Field(
        default=5.0,
        gt=0,
        description="Seconds to acquire a connection from the pool.",
    )

    def to_httpx(self) -> httpx.Timeout:
        """Build an ``httpx.Timeout`` from granular fields."""
        return httpx.Timeout(
            connect=self.connect,
            read=self.read,
            write=self.write,
            pool=self.pool,
        )


def is_retryable_status(status_code: int) -> bool:
    """Return True for statuses classified as transient for idempotent GETs."""
    return status_code in RETRYABLE_HTTP_STATUS


def is_permanent_http_status(status_code: int) -> bool:
    """Return True for non-retryable client errors (4xx except 408/425/429).

    Permanent client failures must never be retried. Server errors outside the
    explicit retry set are also non-retryable via :func:`is_retryable_status`,
    but this helper names the permanent-client-error subset called out by ADR-004.
    """
    return 400 <= status_code < 500 and status_code not in _RETRYABLE_CLIENT_STATUS


def never_retry_status(status_code: int) -> bool:
    """Return True when a response status must not trigger another attempt."""
    return not is_retryable_status(status_code)


# --- Transport (absorbed) ---

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

    Hop-safe semantics (aligned with ``http.hop_safe_request``):

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


def create_http_client(
    *,
    timeout: float,
    accept: str = "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    user_agent_suffix: str = "",
    extra_headers: Mapping[str, str] | None = None,
) -> httpx.Client:
    """Build a hop-safe ``httpx.Client`` (``trust_env=False``, no auto-follow).

    Callers pass this client to :func:`request_with_evidence` /
    :func:`get_with_evidence` / :func:`head_with_evidence`. Validators
    (ETag / Last-Modified) belong on per-request headers, not the client.
    """
    headers: dict[str, str] = {
        "User-Agent": user_agent(user_agent_suffix),
        "Accept": accept,
    }
    if extra_headers:
        headers.update(extra_headers)
    return httpx.Client(
        timeout=httpx.Timeout(timeout),
        follow_redirects=False,
        trust_env=False,
        headers=headers,
    )


def conditional_headers(
    *,
    etag: str | None = None,
    last_modified: str | None = None,
) -> dict[str, str]:
    """Build ``If-None-Match`` / ``If-Modified-Since`` request headers."""
    headers: dict[str, str] = {}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    return headers


# --- Index host policy + hop-safe request (facade) ---

_USER_AGENT = user_agent()
_INDEX_HOSTS = frozenset({"paulgraham.com"})


def _assert_url(url: str) -> None:
    """Index-only host policy (``paulgraham.com`` + http loopback)."""
    parts = urlsplit(url)
    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").lower()
    if not (scheme == "https" or (scheme == "http" and host in _LOOPBACK)):
        raise FeedError(f"Need https (or http loopback): {url!r}")
    if host in _LOOPBACK:
        return
    if _normalize_host(host) not in _INDEX_HOSTS:
        raise FeedError(f"Source host not allowed: {host!r}")


def hop_safe_request(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    allowed_hosts: frozenset[str] | set[str],
    max_bytes: int | None = None,
    max_hops: int = 6,
    allow_loopback: bool | None = None,
) -> httpx.Response:
    """Issue ``method`` with ``follow_redirects=False``, allowlisting every hop.

    When ``max_bytes`` is set, enforce size via Content-Length (when present) and
    a streaming read hard-stop before the body is fully buffered unbounded.

    ``allow_loopback`` defaults from the start URL host (``None`` → start host in
    loopback). That fixed boolean applies to every hop, including the final URL.
    """
    if allow_loopback is None:
        start_host = (urlsplit(url).hostname or "").lower()
        allow_loopback = start_host in _LOOPBACK
    current = url
    response: httpx.Response | None = None
    method_u = method.upper()
    # F-016 / ADR-004: HEAD must not treat representation Content-Length as a
    # downloaded-body budget. Only GET (and other body-bearing methods) apply
    # the transfer cap.
    apply_body_budget = max_bytes is not None and method_u != "HEAD"
    budget = max_bytes if apply_body_budget else None
    for _hop in range(max_hops):
        _assert_hop_allowed(current, allowed_hosts, allow_loopback=allow_loopback)
        logger.debug("{} {}", method_u, current)
        if budget is None:
            req = client.build_request(method_u, current)
            response = client.send(req, stream=True, follow_redirects=False)
            try:
                if response.is_redirect:
                    location = response.headers.get("location")
                    response.close()
                    response = None
                    if not location:
                        raise FeedError(f"Redirect without Location from {current}")
                    current = str(httpx.URL(current).join(location))
                    continue
                response.read()
                break
            except Exception:
                if response is not None:
                    with contextlib.suppress(Exception):
                        response.close()
                raise

        else:
            req = client.build_request(method_u, current)
            response = client.send(req, stream=True, follow_redirects=False)
            try:
                if response.is_redirect:
                    location = response.headers.get("location")
                    response.close()
                    response = None
                    if not location:
                        raise FeedError(f"Redirect without Location from {current}")
                    current = str(httpx.URL(current).join(location))
                    continue
                assert budget is not None
                body = _read_body_capped(response, max_bytes=budget)
                headers = httpx.Headers(response.headers)
                headers.pop("Content-Encoding", None)
                headers.pop("Content-Length", None)
                response = httpx.Response(
                    status_code=response.status_code,
                    headers=headers,
                    content=body,
                    request=response.request,
                    extensions=response.extensions,
                    history=response.history,
                )
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
    if apply_body_budget and budget is not None and len(response.content) > budget:
        raise FeedError(f"Response over {budget} bytes")
    return response


def hop_safe_get(
    client: httpx.Client,
    url: str,
    *,
    allowed_hosts: frozenset[str] | set[str],
    max_bytes: int,
    max_hops: int = 6,
    allow_loopback: bool | None = None,
) -> httpx.Response:
    """GET with hop-validated redirects; enforce ``max_bytes`` on the final body."""
    return hop_safe_request(
        client,
        "GET",
        url,
        allowed_hosts=allowed_hosts,
        max_bytes=max_bytes,
        max_hops=max_hops,
        allow_loopback=allow_loopback,
    )


def _get_once(
    url: str,
    *,
    timeout: float,
    max_bytes: int,
    headers: dict[str, str] | None = None,
) -> str:
    """Single attempt: hop-validated redirects, then body."""
    with create_http_client(
        timeout=timeout,
        accept="text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    ) as client:
        result = get_with_evidence(
            client,
            url,
            allowed_hosts=_INDEX_HOSTS,
            max_bytes=max_bytes,
            headers=headers,
        )
        if result.response is None:
            # Transport failure: raise retryable httpx error (not FeedError) so
            # Tenacity can classify retries before terminal conversion.
            raise httpx.TransportError(result.evidence.error_message or f"Fetch failed for {url}")
        result.response.raise_for_status()
        logger.info("Fetched {} ({} bytes)", result.evidence.final_url, len(result.body))
        charset = result.evidence.charset
        return decode_html(result.body, transport_charset=charset)


def fetch_html(
    url: str = SOURCE_URL,
    *,
    timeout: float = 30.0,
    retries: int = 3,
    max_bytes: int = MAX_BYTES,
    etag: str | None = None,
    last_modified: str | None = None,
) -> str:
    """GET ``url`` and return decoded HTML.

    Retries (Tenacity): exponential backoff + full jitter on transport errors
    and selected HTTP statuses. Permanent :class:`FeedError` cases never retry.
    Redirects are followed only after each hop is re-validated (``trust_env=False``).
    Optional *etag* / *last_modified* become conditional request headers.
    """
    _assert_url(url)
    attempts = max(1, retries + 1)
    headers = conditional_headers(etag=etag, last_modified=last_modified) or None

    def _call() -> str:
        return _get_once(url, timeout=timeout, max_bytes=max_bytes, headers=headers)

    return run_with_retry(_call, attempts=attempts, what=f"fetch {url}")


@dataclass(frozen=True, slots=True)
class IndexFetchResult:
    """Outcome of an index fetch that may be Not Modified (304)."""

    html: str | None
    not_modified: bool
    etag: str | None = None
    last_modified: str | None = None
    status_code: int | None = None
    raw_sha256: str | None = None


def fetch_index(
    url: str = SOURCE_URL,
    *,
    timeout: float = 30.0,
    retries: int = 3,
    max_bytes: int = MAX_BYTES,
    etag: str | None = None,
    last_modified: str | None = None,
) -> IndexFetchResult:
    """Fetch the essays index with optional conditional validators.

    On HTTP 304, ``html`` is ``None`` and ``not_modified`` is True (plan-only path).
    """
    _assert_url(url)
    attempts = max(1, retries + 1)
    cond = conditional_headers(etag=etag, last_modified=last_modified) or None

    def _call() -> IndexFetchResult:
        with create_http_client(
            timeout=timeout,
            accept="text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        ) as client:
            result = get_with_evidence(
                client,
                url,
                allowed_hosts=_INDEX_HOSTS,
                max_bytes=max_bytes,
                headers=cond,
            )
            ev = result.evidence
            if ev.result_kind is ResultKind.NOT_MODIFIED or ev.status_code == 304:
                return IndexFetchResult(
                    html=None,
                    not_modified=True,
                    etag=ev.etag or etag,
                    last_modified=ev.last_modified or last_modified,
                    status_code=304,
                    raw_sha256=None,
                )
            if result.response is None:
                # Keep transport failures retryable until run_with_retry exhausts.
                raise httpx.TransportError(ev.error_message or f"Fetch failed for {url}")
            result.response.raise_for_status()
            html = decode_html(result.body, transport_charset=ev.charset)
            return IndexFetchResult(
                html=html,
                not_modified=False,
                etag=ev.etag,
                last_modified=ev.last_modified,
                status_code=ev.status_code,
                raw_sha256=ev.raw_sha256,
            )

    return run_with_retry(_call, attempts=attempts, what=f"fetch index {url}")


# --- Tenacity retry wrappers ---


def is_retryable_exception(exc: BaseException) -> bool:
    """Return True for transient failures only (never permanent FeedError)."""
    if isinstance(exc, FeedError):
        return False
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRYABLE_HTTP_STATUS
    return isinstance(exc, (httpx.TimeoutException, httpx.TransportError, OSError))


def _before_sleep(retry_state: RetryCallState) -> None:
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    wait = retry_state.next_action.sleep if retry_state.next_action else 0.0
    logger.warning(
        "Retry {attempt} after {exc_type}: {exc} (sleep {wait:.2f}s)",
        attempt=retry_state.attempt_number,
        exc_type=type(exc).__name__ if exc else "?",
        exc=exc,
        wait=wait,
    )


def retrying(*, attempts: int, reraise: bool = True) -> Retrying:
    """Build a Retrying controller: full jitter, honoring bounded Retry-After."""
    n = max(1, attempts)
    return Retrying(
        stop=stop_after_attempt(n),
        wait=wait_retry_after_or_jitter(initial=0.4, max=8.0, exp_base=2),
        retry=retry_if_exception(is_retryable_exception),
        before_sleep=_before_sleep,
        reraise=reraise,
    )


def run_with_retry[T](fn: Callable[[], T], *, attempts: int, what: str) -> T:
    """Execute ``fn`` with tenacity; wrap exhausted retries as NetworkSourceError."""
    try:
        for attempt in retrying(attempts=attempts, reraise=False):
            with attempt:
                return fn()
    except RetryError as exc:
        last = exc.last_attempt.exception()
        if isinstance(last, FeedError):
            raise last from exc
        if isinstance(last, httpx.HTTPStatusError):
            raise NetworkSourceError(
                f"HTTP {last.response.status_code} for {what} after {attempts} attempt(s)"
            ) from last
        raise NetworkSourceError(f"{what} failed after {attempts} attempt(s): {last}") from last
    except FeedError:
        raise
    except httpx.HTTPStatusError as exc:
        raise NetworkSourceError(f"HTTP {exc.response.status_code} for {what}") from exc
    except httpx.HTTPError as exc:
        raise NetworkSourceError(f"{what} failed: {exc}") from exc
    raise NetworkSourceError(f"{what} failed")  # pragma: no cover


__all__ = [
    "DEFAULT_JITTER_EXP_BASE",
    "DEFAULT_JITTER_INITIAL",
    "DEFAULT_JITTER_MAX",
    "DEFAULT_MAX_RETRY_AFTER",
    "RETRYABLE_HTTP_STATUS",
    "SOFT_HTML_MEDIA_TYPES",
    "DecodedDocument",
    "EncodingSource",
    "FetchEvidence",
    "IndexFetchResult",
    "ResultKind",
    "TimeoutConfig",
    "TransportResult",
    "conditional_headers",
    "create_http_client",
    "decode_html",
    "decode_html_document",
    "fetch_html",
    "fetch_index",
    "full_jitter_seconds",
    "get_with_evidence",
    "head_with_evidence",
    "hop_safe_get",
    "hop_safe_request",
    "is_permanent_http_status",
    "is_retryable_exception",
    "is_retryable_status",
    "media_type_is_soft_html",
    "never_retry_status",
    "parse_content_type",
    "parse_retry_after",
    "request_with_evidence",
    "retrying",
    "run_with_retry",
    "wait_full_jitter",
]
