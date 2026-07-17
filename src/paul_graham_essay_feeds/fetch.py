"""Fetch the essays index with httpx + Tenacity retries."""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from urllib.parse import urlsplit

import httpx
from loguru import logger
from tenacity import (
    RetryCallState,
    RetryError,
    Retrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from paul_graham_essay_feeds.model import MAX_BYTES, SOURCE_URL, FeedError, user_agent

_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
_USER_AGENT = user_agent()
_INDEX_HOSTS = frozenset({"paulgraham.com"})


def _normalize_host(host: str) -> str:
    host = host.lower()
    return "paulgraham.com" if host == "www.paulgraham.com" else host


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


def decode_html(body: bytes) -> str:
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("latin-1")


def _content_length(response: httpx.Response) -> int | None:
    """Parse ``Content-Length`` when present and valid; else ``None``."""
    raw = response.headers.get("content-length")
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _read_body_capped(response: httpx.Response, *, max_bytes: int) -> bytes:
    """Read response body with a hard size cap (Content-Length + stream).

    Raises :class:`FeedError` if the declared or actual body exceeds ``max_bytes``.
    """
    declared = _content_length(response)
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
    for _hop in range(max_hops):
        _assert_hop_allowed(current, allowed_hosts, allow_loopback=allow_loopback)
        logger.debug("{} {}", method_u, current)
        if max_bytes is None:
            # Stream so redirect responses can be closed without buffering a body.
            req = client.build_request(method_u, current)
            response = client.send(req, stream=True)
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
            # Size-capped path: stream so oversize bodies fail without full buffer.
            req = client.build_request(method_u, current)
            response = client.send(req, stream=True)
            try:
                if response.is_redirect:
                    location = response.headers.get("location")
                    response.close()
                    response = None
                    if not location:
                        raise FeedError(f"Redirect without Location from {current}")
                    current = str(httpx.URL(current).join(location))
                    continue
                body = _read_body_capped(response, max_bytes=max_bytes)
                response = httpx.Response(
                    status_code=response.status_code,
                    headers=response.headers,
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
    if max_bytes is not None and len(response.content) > max_bytes:
        raise FeedError(f"Response over {max_bytes} bytes")
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


def _get_once(url: str, *, timeout: float, max_bytes: int) -> str:
    """Single attempt: hop-validated redirects, then body."""
    with httpx.Client(
        timeout=httpx.Timeout(timeout),
        follow_redirects=False,
        trust_env=False,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        },
    ) as client:
        response = hop_safe_get(
            client,
            url,
            allowed_hosts=_INDEX_HOSTS,
            max_bytes=max_bytes,
        )
        # Let tenacity retry on retryable status via HTTPStatusError.
        response.raise_for_status()
        logger.info("Fetched {} ({} bytes)", response.url, len(response.content))
        return decode_html(response.content)


def fetch_html(
    url: str = SOURCE_URL,
    *,
    timeout: float = 30.0,
    retries: int = 3,
    max_bytes: int = MAX_BYTES,
) -> str:
    """GET ``url`` and return decoded HTML.

    Retries (Tenacity): exponential backoff + full jitter on transport errors
    and selected HTTP statuses. Permanent :class:`FeedError` cases never retry.
    Redirects are followed only after each hop is re-validated (``trust_env=False``).
    """
    _assert_url(url)
    attempts = max(1, retries + 1)

    def _call() -> str:
        return _get_once(url, timeout=timeout, max_bytes=max_bytes)

    return run_with_retry(_call, attempts=attempts, what=f"fetch {url}")


# --- Tenacity retry (absorbed from retry.py) ---

# Status codes worth retrying for idempotent GETs.
RETRYABLE_HTTP_STATUS: frozenset[int] = frozenset({408, 425, 429, 500, 502, 503, 504})


def is_retryable_exception(exc: BaseException) -> bool:
    """Return True for transient failures only (never permanent FeedError)."""
    if isinstance(exc, FeedError):
        return False
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRYABLE_HTTP_STATUS
    # Timeouts, connect errors, read errors, protocol errors, OS-level blips.
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
    """Build a Retrying controller: exponential backoff + full jitter."""
    n = max(1, attempts)
    return Retrying(
        stop=stop_after_attempt(n),
        wait=wait_exponential_jitter(initial=0.4, max=8.0, exp_base=2, jitter=0.5),
        retry=retry_if_exception(is_retryable_exception),
        before_sleep=_before_sleep,
        reraise=reraise,
    )


def run_with_retry[T](fn: Callable[[], T], *, attempts: int, what: str) -> T:
    """Execute ``fn`` with tenacity; wrap exhausted retries as FeedError."""
    try:
        # reraise=False → RetryError on exhaustion (so we can attach attempt count).
        for attempt in retrying(attempts=attempts, reraise=False):
            with attempt:
                return fn()
    except RetryError as exc:
        last = exc.last_attempt.exception()
        if isinstance(last, FeedError):
            raise last from exc
        if isinstance(last, httpx.HTTPStatusError):
            raise FeedError(
                f"HTTP {last.response.status_code} for {what} after {attempts} attempt(s)"
            ) from last
        raise FeedError(f"{what} failed after {attempts} attempt(s): {last}") from last
    except FeedError:
        raise
    except httpx.HTTPStatusError as exc:
        raise FeedError(f"HTTP {exc.response.status_code} for {what}") from exc
    except httpx.HTTPError as exc:
        raise FeedError(f"{what} failed: {exc}") from exc
    raise FeedError(f"{what} failed")  # pragma: no cover
