"""Retry-After parsing, true full jitter, granular timeouts (ADR-004 / F-022).

This module is intentionally pure policy — callers (fetch wiring) consume these
helpers. It does not perform HTTP I/O.

Full jitter means: sample uniformly from ``[0, min(cap, initial * base ** n)]``.
That is **not** additive jitter (``exp + random(0, j)``), which tenacity names
``wait_exponential_jitter``. Prefer this module's ``wait_full_jitter`` (or
``full_jitter_seconds``) when documenting/using full jitter semantics.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Final

import httpx
from pydantic import BaseModel, ConfigDict, Field
from tenacity import RetryCallState

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
