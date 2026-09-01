"""Unified HTTP facade: hop-safe transport, decode, retry, index fetch (AD-004).

Owns transport evidence, HTML decoding, retry policy, and :func:`fetch_index` /
:func:`fetch_html`. Leaf modules were folded into this module.
"""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import importlib
import random
import re
import threading
import time
import zlib
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
_REDIRECT_STATUS: Final = frozenset({301, 302, 303, 307, 308})
_HTTPS_PORT: Final = 443

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

    ``raw_sha256`` / ``bytes_received`` are **wire** (pre-content-decode).
    ``decoded_sha256`` / ``decoded_bytes_received`` are the entity after
    Content-Encoding is removed. They match for identity encoding.
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
    decoded_sha256: str | None = None
    decoded_bytes_received: int = 0
    error_message: str | None = None
    redirect_urls: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TransportResult:
    """Outcome of :func:`request_with_evidence`.

    ``response`` is a fully buffered :class:`httpx.Response` when the exchange
    completed (including non-2xx). ``body`` is the **decoded entity** for
    methods that transfer a body (empty for HEAD / 304). ``raw_body`` is the
    wire bytes (pre-content-decode).
    """

    evidence: FetchEvidence
    response: httpx.Response | None = None
    body: bytes = b""
    raw_body: bytes = b""


@dataclass(frozen=True, slots=True)
class _BodyBuffers:
    """Wire bytes plus content-decoded entity (AUD-007)."""

    raw: bytes
    decoded: bytes


@dataclass(frozen=True, slots=True)
class _HopExchange:
    """One hop-safe exchange (final non-redirect response)."""

    response: httpx.Response
    decoded: bytes
    raw: bytes
    header_snap: dict[str, str | None]
    final_url: str
    status_code: int
    redirect_urls: tuple[str, ...]
    request_headers: dict[str, str]


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


def _idna_hostname(host: str) -> str:
    """Lowercase, strip trailing dots, IDNA-encode (leave IPs unchanged)."""
    host = host.strip().rstrip(".").lower()
    if not host:
        return host
    if ":" in host:
        return host
    try:
        return host.encode("idna").decode("ascii").lower().rstrip(".")
    except (UnicodeError, UnicodeDecodeError, UnicodeEncodeError):
        return host


def _normalize_host(host: str) -> str:
    host = _idna_hostname(host)
    return "paulgraham.com" if host == "www.paulgraham.com" else host


def _etag_key(value: str) -> str:
    """Compare ETags ignoring weak-prefix and surrounding quotes."""
    text = value.strip()
    if text[:2].upper() == "W/":
        text = text[2:].strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        text = text[1:-1]
    return text


def _if_none_match_covers(header: str, etag: str) -> bool:
    token = header.strip()
    if token == "*":
        return True
    wanted = _etag_key(etag)
    return any(_etag_key(part) == wanted for part in token.split(",") if part.strip())


def _header_ci(headers: Mapping[str, str] | None, name: str) -> str | None:
    if not headers:
        return None
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            stripped = value.strip()
            return stripped or None
    return None


def _304_is_acceptable(
    *,
    request_headers: Mapping[str, str] | None,
    prior_etag: str | None,
    prior_last_modified: str | None,
    prior_body_hash: str | None,
    response_etag: str | None,
) -> bool:
    """AUD-016: 304 is success only with conditionals actually sent and prior material.

    Inspect the headers on the hop that received the 304 (PGF-2026-011), not the
    headers the caller originally intended. An unconditional 304 is never
    acceptable, even when an earlier hop carried validators.
    """
    inm = _header_ci(request_headers, "If-None-Match")
    ims = _header_ci(request_headers, "If-Modified-Since")
    if inm is None and ims is None:
        return False
    if not (prior_etag or prior_last_modified or prior_body_hash):
        return False
    inm_ok = True
    if inm is not None and response_etag:
        inm_ok = _if_none_match_covers(inm, response_etag)
    prior_ok = True
    if prior_etag and response_etag:
        prior_ok = _etag_key(prior_etag) == _etag_key(response_etag)
    return inm_ok and prior_ok


def _assert_hop_allowed(
    url: str,
    allowed_hosts: frozenset[str] | set[str],
    *,
    allow_loopback: bool,
) -> None:
    """Raise FeedError unless ``url`` scheme/host/port is permitted for this hop.

    Tightened hop policy (AUD-008): reject userinfo, fragments, non-443 HTTPS
    ports, percent-encoded hosts. HTTP loopback may use any explicit port only
    when ``allow_loopback`` is True. Hostnames are IDNA-normalized.
    """
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise FeedError(f"Invalid URL: {url!r}") from exc

    if parts.fragment or "#" in url:
        raise FeedError(f"Fragment not allowed: {url!r}")
    if parts.username is not None or parts.password is not None or "@" in (parts.netloc or ""):
        raise FeedError(f"Userinfo not allowed: {url!r}")

    scheme = (parts.scheme or "").lower()
    raw_host = parts.hostname or ""
    if not raw_host:
        raise FeedError(f"URL missing host: {url!r}")
    if "%" in raw_host:
        raise FeedError(f"Encoded host not allowed: {url!r}")

    try:
        port = parts.port
    except (ValueError, OverflowError) as exc:
        raise FeedError(f"Invalid port: {url!r}") from exc

    host = _normalize_host(raw_host)
    is_loopback = host in _LOOPBACK

    if is_loopback:
        if not allow_loopback:
            raise FeedError(f"Host not allowed: {host!r}")
        if scheme not in {"http", "https"}:
            raise FeedError(f"Need https (or http loopback): {url!r}")
        if scheme == "https" and port is not None and port != _HTTPS_PORT:
            raise FeedError(f"Port not allowed: {url!r}")
        return

    if scheme != "https":
        raise FeedError(f"Need https (or http loopback): {url!r}")
    if port is not None and port != _HTTPS_PORT:
        raise FeedError(f"Port not allowed: {url!r}")
    allowed = {_normalize_host(h) for h in allowed_hosts}
    if host not in allowed:
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


_MAX_ENCODING_LAYERS: Final = 4


def _brotli_decompress(data: bytes, *, max_bytes: int | None = None) -> bytes:
    module = None
    with contextlib.suppress(ImportError):
        module = importlib.import_module("brotli")
    if module is None:
        with contextlib.suppress(ImportError):
            module = importlib.import_module("brotlicffi")
    if module is None:
        raise FeedError("Unsupported Content-Encoding: br (brotli package not installed)")
    decompressor_cls = getattr(module, "Decompressor", None)
    if callable(decompressor_cls) and max_bytes is not None:
        dec = decompressor_cls()
        out = bytearray()
        process = getattr(dec, "process", None) or getattr(dec, "decompress", None)
        if not callable(process):
            raise FeedError("Unsupported Content-Encoding: br")
        chunk = process(data)
        out.extend(chunk)
        if len(out) > max_bytes:
            raise FeedError(f"Response over {max_bytes} bytes")
        is_finished = getattr(dec, "is_finished", None)
        if callable(is_finished) and not is_finished():
            finish = getattr(dec, "finish", None)
            if callable(finish):
                out.extend(finish())
        if len(out) > max_bytes:
            raise FeedError(f"Response over {max_bytes} bytes")
        return bytes(out)
    decompress = getattr(module, "decompress", None)
    if not callable(decompress):
        raise FeedError("Unsupported Content-Encoding: br")
    body = bytes(decompress(data))
    if max_bytes is not None and len(body) > max_bytes:
        raise FeedError(f"Response over {max_bytes} bytes")
    return body


def _zlib_decompress_capped(data: bytes, *, wbits: int, max_bytes: int) -> bytes:
    """Incrementally inflate gzip/zlib/raw-deflate and abort past ``max_bytes``."""
    dec = zlib.decompressobj(wbits)
    out = bytearray()
    remaining = data
    while remaining:
        budget = max_bytes - len(out)
        if budget <= 0:
            raise FeedError(f"Response over {max_bytes} bytes")
        chunk = dec.decompress(remaining, budget)
        out.extend(chunk)
        if len(out) > max_bytes:
            raise FeedError(f"Response over {max_bytes} bytes")
        leftover = dec.unconsumed_tail
        if leftover == remaining:
            break
        remaining = leftover
    out.extend(dec.flush())
    if len(out) > max_bytes:
        raise FeedError(f"Response over {max_bytes} bytes")
    return bytes(out)


def _content_encoding_tokens(content_encoding: str | None) -> list[str]:
    """Split a Content-Encoding header into lowercase coding tokens."""
    if not content_encoding or not content_encoding.strip():
        return []
    return [t.strip().lower() for t in content_encoding.split(",") if t.strip()]


def _decode_content_encoding(
    raw: bytes,
    content_encoding: str | None,
    *,
    max_bytes: int | None = None,
) -> bytes:
    """Undo Content-Encoding (gzip / deflate / br) in reverse application order.

    Missing or ``identity`` is a no-op. Every declared non-identity token must
    be supported; unknown encodings fail closed and are never treated as
    identity (PGF-2026-017). Empty bodies skip decompression after that check
    so HEAD/304 cannot be misread as a successful identity decode. When
    ``max_bytes`` is set, gzip/deflate inflate incrementally and abort before
    assembling a larger decoded payload.
    """
    tokens = _content_encoding_tokens(content_encoding)
    if not tokens:
        if max_bytes is not None and len(raw) > max_bytes:
            raise FeedError(f"Response over {max_bytes} bytes")
        return raw
    layers = [t for t in tokens if t != "identity"]
    if len(layers) > _MAX_ENCODING_LAYERS:
        raise FeedError(f"Too many Content-Encoding layers ({len(layers)})")
    body = raw
    for token in reversed(tokens):
        if token == "identity":
            continue
        if token not in {"gzip", "x-gzip", "deflate", "br", "brotli"}:
            raise FeedError(f"Unsupported Content-Encoding: {token}")
        if not body:
            continue
        if token in {"gzip", "x-gzip"}:
            if max_bytes is None:
                body = gzip.decompress(body)
            else:
                body = _zlib_decompress_capped(body, wbits=16 + zlib.MAX_WBITS, max_bytes=max_bytes)
        elif token == "deflate":
            if max_bytes is None:
                try:
                    body = zlib.decompress(body)
                except zlib.error:
                    body = zlib.decompress(body, -zlib.MAX_WBITS)
            else:
                try:
                    body = _zlib_decompress_capped(body, wbits=zlib.MAX_WBITS, max_bytes=max_bytes)
                except zlib.error:
                    body = _zlib_decompress_capped(body, wbits=-zlib.MAX_WBITS, max_bytes=max_bytes)
        else:
            body = _brotli_decompress(body, max_bytes=max_bytes)  # type: ignore[misc]
        if max_bytes is not None and len(body) > max_bytes:
            raise FeedError(f"Response over {max_bytes} bytes")
    return body


def _consume_entity_body(
    response: httpx.Response,
    *,
    max_bytes: int | None,
) -> _BodyBuffers:
    """Read **wire** bytes (capped), then content-decode into the entity buffer.

    ``Content-Length`` (when present) is the wire size. The same ``max_bytes``
    cap applies to decoded entity bytes (zip-bomb stop).
    """
    if max_bytes is not None:
        declared = _parse_content_length(response)
        if declared is not None and declared > max_bytes:
            response.close()
            raise FeedError(f"Response over {max_bytes} bytes")
    raw_buf = bytearray()
    try:
        for chunk in response.iter_raw():
            if not chunk:
                continue
            raw_buf.extend(chunk)
            if max_bytes is not None and len(raw_buf) > max_bytes:
                raise FeedError(f"Response over {max_bytes} bytes")
    finally:
        with contextlib.suppress(Exception):
            response.close()
    raw = bytes(raw_buf)
    encoding = response.headers.get("content-encoding")
    try:
        decoded = _decode_content_encoding(raw, encoding, max_bytes=max_bytes)
    except FeedError:
        raise
    except (OSError, gzip.BadGzipFile, zlib.error, ValueError) as exc:
        raise FeedError(f"Failed to decode Content-Encoding: {exc}") from exc
    if max_bytes is not None and len(decoded) > max_bytes:
        raise FeedError(f"Response over {max_bytes} bytes")
    return _BodyBuffers(raw=raw, decoded=decoded)


def _read_body_capped(response: httpx.Response, *, max_bytes: int) -> bytes:
    """Read response body with a hard size cap (Content-Length + stream).

    Returns **decoded** entity bytes. Wire bytes are hashed separately in
    :func:`request_with_evidence`. Raises :class:`FeedError` if the declared
    wire size or actual wire/decoded body exceeds ``max_bytes``.
    Only for methods that transfer an entity body (GET).
    """
    return _consume_entity_body(response, max_bytes=max_bytes).decoded


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
    decoded_sha256: str | None = None,
    decoded_bytes_received: int = 0,
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
        decoded_sha256=decoded_sha256,
        decoded_bytes_received=decoded_bytes_received,
        error_message=error_message,
        redirect_urls=redirect_urls,
    )


def _build_evidence(
    *,
    method: str,
    requested_url: str,
    final_url: str,
    status_code: int,
    raw: bytes,
    decoded: bytes,
    header_snap: Mapping[str, str | None],
    redirect_urls: tuple[str, ...],
    not_modified_ok: bool = False,
) -> FetchEvidence:
    media_type, charset = parse_content_type(header_snap.get("content-type"))
    cl = _parse_content_length_value(header_snap.get("content-length"))
    bytes_received = len(raw)
    decoded_bytes_received = len(decoded)
    raw_sha256 = hashlib.sha256(raw).hexdigest() if raw else None
    decoded_sha256 = hashlib.sha256(decoded).hexdigest() if decoded else None
    if status_code == 304:
        if not_modified_ok:
            kind = ResultKind.NOT_MODIFIED
            error_message = None
        else:
            kind = ResultKind.FAILED
            error_message = "Unacceptable HTTP 304"
    elif status_code == 200:
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
        decoded_sha256=decoded_sha256,
        decoded_bytes_received=decoded_bytes_received,
        error_message=error_message,
        redirect_urls=redirect_urls,
    )


def _join_redirect(current: str, location: str) -> str:
    """Resolve Location against the current hop and re-validate later."""
    return str(httpx.URL(current).join(location))


def _hop_exchange(
    client: httpx.Client,
    method_u: str,
    url: str,
    *,
    allowed_hosts: frozenset[str] | set[str],
    max_bytes: int | None,
    max_hops: int,
    allow_loopback: bool,
    headers: Mapping[str, str] | None,
) -> _HopExchange:
    """Issue *method_u* with hop-validated redirects; return the final exchange.

    Raises :class:`FeedError` for policy violations. Transport errors propagate.
    """
    apply_body_budget = max_bytes is not None and method_u != "HEAD"
    current = url
    redirect_chain: list[str] = []
    response: httpx.Response | None = None
    extra_headers = dict(headers) if headers else None
    header_snap: dict[str, str | None] = {}
    sent_headers: dict[str, str] = dict(extra_headers) if extra_headers else {}
    final_url = url
    status_code = 0
    raw = b""
    decoded = b""

    for _hop in range(max_hops):
        _assert_hop_allowed(current, allowed_hosts, allow_loopback=allow_loopback)
        logger.debug("{} {}", method_u, current)
        req = client.build_request(method_u, current, headers=extra_headers)
        sent_headers = {str(k): str(v) for k, v in req.headers.items()}
        response = client.send(req, stream=True, follow_redirects=False)
        try:
            if response.status_code in _REDIRECT_STATUS:
                location = response.headers.get("location")
                response.close()
                response = None
                if not location:
                    raise FeedError(f"Redirect without Location from {current}")
                next_url = _join_redirect(current, location)
                _assert_hop_allowed(next_url, allowed_hosts, allow_loopback=allow_loopback)
                redirect_chain.append(next_url)
                current = next_url
                extra_headers = None
                continue

            header_snap = _header_snapshot(response)
            final_url = str(response.url)
            status_code = response.status_code

            if method_u == "HEAD":
                response.close()
                decoded = b""
                raw = b""
                buffered = _rebuild_response(response, body=decoded)
                response = buffered
            elif apply_body_budget:
                buffers = _consume_entity_body(response, max_bytes=max_bytes)
                raw = buffers.raw
                decoded = buffers.decoded
                buffered = _rebuild_response(response, body=decoded)
                response = buffered
            else:
                buffers = _consume_entity_body(response, max_bytes=None)
                raw = buffers.raw
                decoded = buffers.decoded
                buffered = _rebuild_response(response, body=decoded)
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
    if (
        apply_body_budget
        and max_bytes is not None
        and (len(raw) > max_bytes or len(decoded) > max_bytes)
    ):
        raise FeedError(f"Response over {max_bytes} bytes")

    return _HopExchange(
        response=response,
        decoded=decoded,
        raw=raw,
        header_snap=header_snap,
        final_url=final_url,
        status_code=status_code,
        redirect_urls=tuple(redirect_chain),
        request_headers=sent_headers,
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
    prior_etag: str | None = None,
    prior_last_modified: str | None = None,
    prior_body_hash: str | None = None,
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
      via ``Content-Length`` (when present) and a streaming hard-stop on **wire**
      bytes, then content-decode into a second buffer.

    HTTP 304 (**AUD-016** / **PGF-2026-011**) is ``NOT_MODIFIED`` only when the
    **final hop** actually sent ``If-None-Match`` and/or ``If-Modified-Since``
    **and** the caller supplied prior material (``prior_etag`` /
    ``prior_last_modified`` / ``prior_body_hash``). Redirect hops drop
    per-request extras, so a 304 after redirect is classified from the headers
    sent on that hop — never from the original request. An unconditional
    final-hop 304 is never ``NOT_MODIFIED``. Otherwise a GET retries once
    without conditionals; a second empty/304 outcome is ``FAILED``.

    Raises :class:`FeedError` for policy violations (disallowed host, too many
    redirects, oversize GET body). Transport-level exceptions become
    ``result_kind=failed`` evidence (no raise).
    """
    if allow_loopback is None:
        start_host = _normalize_host(urlsplit(url).hostname or "")
        allow_loopback = start_host in _LOOPBACK

    method_u = method.upper()
    retrying_unconditional = False
    active_headers = dict(headers) if headers else None

    try:
        while True:
            exchange = _hop_exchange(
                client,
                method_u,
                url,
                allowed_hosts=allowed_hosts,
                max_bytes=max_bytes,
                max_hops=max_hops,
                allow_loopback=allow_loopback,
                headers=active_headers,
            )

            if retrying_unconditional and not exchange.decoded:
                return TransportResult(
                    evidence=_failed_evidence(
                        method=method_u,
                        requested_url=url,
                        final_url=exchange.final_url,
                        status_code=exchange.status_code,
                        error_message="Unacceptable HTTP 304",
                        redirect_urls=exchange.redirect_urls,
                        etag=exchange.header_snap.get("etag"),
                        last_modified=exchange.header_snap.get("last-modified"),
                        content_length_header=_parse_content_length_value(
                            exchange.header_snap.get("content-length")
                        ),
                    ),
                    response=exchange.response,
                    body=b"",
                    raw_body=exchange.raw,
                )

            not_modified_ok = False
            if exchange.status_code == 304:
                not_modified_ok = _304_is_acceptable(
                    request_headers=exchange.request_headers,
                    prior_etag=prior_etag,
                    prior_last_modified=prior_last_modified,
                    prior_body_hash=prior_body_hash,
                    response_etag=exchange.header_snap.get("etag"),
                )
                if not not_modified_ok and method_u == "GET" and not retrying_unconditional:
                    retrying_unconditional = True
                    active_headers = None
                    continue

            evidence = _build_evidence(
                method=method_u,
                requested_url=url,
                final_url=exchange.final_url,
                status_code=exchange.status_code,
                raw=exchange.raw,
                decoded=exchange.decoded,
                header_snap=exchange.header_snap,
                redirect_urls=exchange.redirect_urls,
                not_modified_ok=not_modified_ok,
            )
            return TransportResult(
                evidence=evidence,
                response=exchange.response,
                body=exchange.decoded,
                raw_body=exchange.raw,
            )

    except httpx.HTTPError as exc:
        return TransportResult(
            evidence=_failed_evidence(
                method=method_u,
                requested_url=url,
                final_url=url,
                status_code=None,
                error_message=str(exc),
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
    prior_etag: str | None = None,
    prior_last_modified: str | None = None,
    prior_body_hash: str | None = None,
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
        prior_etag=prior_etag,
        prior_last_modified=prior_last_modified,
        prior_body_hash=prior_body_hash,
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
    try:
        start_host = _normalize_host(urlsplit(url).hostname or "")
    except ValueError as exc:
        raise FeedError(f"Invalid URL: {url!r}") from exc
    allow_loopback = start_host in _LOOPBACK
    _assert_hop_allowed(url, _INDEX_HOSTS, allow_loopback=allow_loopback)


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
        start_host = _normalize_host(urlsplit(url).hostname or "")
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
                    _assert_hop_allowed(current, allowed_hosts, allow_loopback=allow_loopback)
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
                    _assert_hop_allowed(current, allowed_hosts, allow_loopback=allow_loopback)
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
    prior_etag: str | None = None,
    prior_last_modified: str | None = None,
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
            prior_etag=prior_etag,
            prior_last_modified=prior_last_modified,
        )
        ev = result.evidence
        if ev.result_kind is ResultKind.NOT_MODIFIED:
            raise FeedError(f"HTTP 304 with no body for {url}")
        if ev.result_kind is ResultKind.FAILED:
            if result.response is None:
                raise httpx.TransportError(ev.error_message or f"Fetch failed for {url}")
            if ev.status_code == 304:
                raise FeedError(ev.error_message or "Unacceptable HTTP 304")
            result.response.raise_for_status()
            raise FeedError(ev.error_message or f"HTTP {ev.status_code}")
        if result.response is None:
            raise httpx.TransportError(ev.error_message or f"Fetch failed for {url}")
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
        return _get_once(
            url,
            timeout=timeout,
            max_bytes=max_bytes,
            headers=headers,
            prior_etag=etag,
            prior_last_modified=last_modified,
        )

    return run_with_retry(_call, attempts=attempts, what=f"fetch {url}")


class HostCooldown:
    """Minimum inter-request gap for a single host (RV-R-005).

    ``seconds <= 0`` disables waiting. Clock, sleeper, and RNG are injectable
    for tests. Optional ``jitter`` (seconds) is added on top of the remaining
    gap: ``sleep(remaining + random * jitter)``. Default jitter is ``0`` so
    existing tests stay deterministic. Thread-safe for concurrent enrich /
    probe workers.
    """

    def __init__(
        self,
        seconds: float,
        *,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
        jitter: float = 0.0,
        rng: random.Random | None = None,
    ) -> None:
        self.seconds = float(seconds)
        self.jitter = max(0.0, float(jitter))
        self._clock = clock or time.monotonic
        self._sleeper = sleeper or time.sleep
        self._rng = rng
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, host: str) -> None:
        """Block until ``seconds`` have elapsed since the last wait for ``host``."""
        if self.seconds <= 0:
            return
        host_key = host.strip().lower() or host
        with self._lock:
            now = self._clock()
            last = self._last.get(host_key)
            if last is not None:
                remaining = self.seconds - (now - last)
                extra = 0.0
                if self.jitter > 0:
                    draw = self._rng.random() if self._rng is not None else random.random()
                    extra = draw * self.jitter
                sleep_for = max(0.0, remaining) + extra
                if sleep_for > 0:
                    self._sleeper(sleep_for)
                    now = self._clock()
            self._last[host_key] = now


@dataclass(frozen=True, slots=True)
class IndexFetchResult:
    """Outcome of an index fetch that may be Not Modified (304)."""

    html: str | None
    not_modified: bool
    etag: str | None = None
    last_modified: str | None = None
    status_code: int | None = None
    raw_sha256: str | None = None
    decoded_sha256: str | None = None
    raw_bytes_received: int | None = None
    decoded_bytes_received: int | None = None
    selected_encoding: str | None = None


def fetch_index(
    url: str = SOURCE_URL,
    *,
    timeout: float = 30.0,
    retries: int = 3,
    max_bytes: int = MAX_BYTES,
    etag: str | None = None,
    last_modified: str | None = None,
    prior_body_hash: str | None = None,
) -> IndexFetchResult:
    """Fetch the essays index with optional conditional validators.

    On an *acceptable* HTTP 304, ``html`` is ``None`` and ``not_modified`` is
    True (plan-only path). Unacceptable 304 (no conditionals / no prior
    material) is not treated as success: transport retries once unconditionally,
    then raises :class:`FeedError`.
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
                prior_etag=etag,
                prior_last_modified=last_modified,
                prior_body_hash=prior_body_hash,
            )
            ev = result.evidence
            if ev.result_kind is ResultKind.NOT_MODIFIED:
                return IndexFetchResult(
                    html=None,
                    not_modified=True,
                    etag=ev.etag or etag,
                    last_modified=ev.last_modified or last_modified,
                    status_code=304,
                    raw_sha256=None,
                    decoded_sha256=None,
                    raw_bytes_received=ev.bytes_received,
                    decoded_bytes_received=ev.decoded_bytes_received,
                    selected_encoding=None,
                )
            if ev.result_kind is ResultKind.FAILED:
                if result.response is None:
                    raise httpx.TransportError(ev.error_message or f"Fetch failed for {url}")
                if ev.status_code == 304:
                    raise FeedError(ev.error_message or "Unacceptable HTTP 304")
                result.response.raise_for_status()
                raise FeedError(ev.error_message or f"HTTP {ev.status_code}")
            if result.response is None:
                raise httpx.TransportError(ev.error_message or f"Fetch failed for {url}")
            result.response.raise_for_status()
            if ev.status_code != 200:
                raise FeedError(f"HTTP {ev.status_code} is not a usable index document")
            if not result.body:
                raise FeedError(f"Empty HTTP 200 body for {url}")
            document = decode_html_document(result.body, transport_charset=ev.charset)
            return IndexFetchResult(
                html=document.text,
                not_modified=False,
                etag=ev.etag,
                last_modified=ev.last_modified,
                status_code=ev.status_code,
                raw_sha256=ev.raw_sha256,
                decoded_sha256=ev.decoded_sha256,
                raw_bytes_received=ev.bytes_received,
                decoded_bytes_received=ev.decoded_bytes_received,
                selected_encoding=document.encoding,
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
    "HostCooldown",
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
