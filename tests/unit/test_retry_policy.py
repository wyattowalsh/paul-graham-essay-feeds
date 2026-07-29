"""Unit tests for ADR-004 retry policy (Retry-After, full jitter, timeouts)."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta, timezone
from email.utils import format_datetime
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from paul_graham_essay_feeds.http import (
    DEFAULT_MAX_RETRY_AFTER,
    RETRYABLE_HTTP_STATUS,
    TimeoutConfig,
    full_jitter_seconds,
    is_permanent_http_status,
    is_retryable_status,
    never_retry_status,
    parse_retry_after,
    wait_full_jitter,
)


def test_parse_retry_after_delta_seconds() -> None:
    assert parse_retry_after("0") == 0.0
    assert parse_retry_after("30") == 30.0
    assert parse_retry_after("  45  ") == 45.0
    assert parse_retry_after("+10") == 10.0


def test_parse_retry_after_http_date() -> None:
    now = datetime(2026, 7, 25, 12, 0, 0, tzinfo=UTC)
    future = now + timedelta(seconds=42)
    header = format_datetime(future, usegmt=True)
    wait = parse_retry_after(header, now=now)
    assert wait is not None
    assert wait == pytest.approx(42.0, abs=0.01)


def test_parse_retry_after_http_date_past_is_zero() -> None:
    now = datetime(2026, 7, 25, 12, 0, 0, tzinfo=UTC)
    past = now - timedelta(seconds=30)
    header = format_datetime(past, usegmt=True)
    assert parse_retry_after(header, now=now) == 0.0


def test_parse_retry_after_cap_enforcement() -> None:
    assert parse_retry_after("9999") == DEFAULT_MAX_RETRY_AFTER
    assert parse_retry_after("200", max_wait=50.0) == 50.0

    now = datetime(2026, 7, 25, 12, 0, 0, tzinfo=UTC)
    far = now + timedelta(seconds=500)
    header = format_datetime(far, usegmt=True)
    assert parse_retry_after(header, now=now, max_wait=60.0) == 60.0


def test_parse_retry_after_invalid_and_empty() -> None:
    assert parse_retry_after(None) is None
    assert parse_retry_after("") is None
    assert parse_retry_after("   ") is None
    assert parse_retry_after("not-a-date") is None
    assert parse_retry_after("12.5") is None  # not integer delta-seconds


def test_non_retryable_classification() -> None:
    for code in (408, 425, 429, 500, 502, 503, 504):
        assert is_retryable_status(code) is True
        assert never_retry_status(code) is False
        assert code in RETRYABLE_HTTP_STATUS

    for code in (400, 401, 403, 404, 405, 409, 410, 418, 422, 451):
        assert is_permanent_http_status(code) is True
        assert is_retryable_status(code) is False
        assert never_retry_status(code) is True

    # Retryable client codes are not "permanent".
    for code in (408, 425, 429):
        assert is_permanent_http_status(code) is False

    # 501 Not Implemented: not in retry set.
    assert is_retryable_status(501) is False
    assert never_retry_status(501) is True
    assert is_permanent_http_status(501) is False  # not a 4xx permanent client error

    # Success / redirect are not retryable.
    assert is_retryable_status(200) is False
    assert never_retry_status(200) is True


def test_full_jitter_bounds_with_fixed_rng() -> None:
    rng = random.Random(0)
    # attempt 1 → high = min(8, 0.4 * 2**0) = 0.4
    for _ in range(50):
        wait = full_jitter_seconds(1, initial=0.4, max_wait=8.0, exp_base=2.0, rng=rng)
        assert 0.0 <= wait <= 0.4

    # attempt 5 → high = min(8, 0.4 * 2**4) = min(8, 6.4) = 6.4
    rng = random.Random(1)
    for _ in range(50):
        wait = full_jitter_seconds(5, initial=0.4, max_wait=8.0, exp_base=2.0, rng=rng)
        assert 0.0 <= wait <= 6.4

    # Cap at max_wait: attempt 10 → 0.4 * 2**9 = 204.8 → capped to 8.0
    rng = random.Random(2)
    for _ in range(50):
        wait = full_jitter_seconds(10, initial=0.4, max_wait=8.0, exp_base=2.0, rng=rng)
        assert 0.0 <= wait <= 8.0


def test_full_jitter_is_not_additive_only() -> None:
    """Full jitter must be able to return values near zero, not exp+offset."""
    rng = random.Random(42)
    # With a large high bound, samples must include values well below the high
    # bound (true full jitter), not cluster at high ± small additive jitter.
    samples = [
        full_jitter_seconds(8, initial=1.0, max_wait=64.0, exp_base=2.0, rng=rng)
        for _ in range(200)
    ]
    high = min(64.0, 1.0 * (2.0**7))  # attempt 8 → 1 * 2**7 = 128 → 64
    assert all(0.0 <= s <= high for s in samples)
    assert min(samples) < high * 0.25
    assert max(samples) > high * 0.5


def test_wait_full_jitter_tenacity_callable() -> None:
    rng = random.Random(7)
    wait = wait_full_jitter(initial=1.0, max=4.0, exp_base=2.0, rng=rng)
    # Mimic tenacity RetryCallState with attempt_number only.
    state = SimpleNamespace(attempt_number=3)
    # high = min(4, 1 * 2**2) = 4
    value = wait(state)  # ty: ignore[invalid-argument-type]
    assert 0.0 <= value <= 4.0


def test_timeout_config_to_httpx() -> None:
    cfg = TimeoutConfig()
    timeout = cfg.to_httpx()
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.connect == 5.0
    assert timeout.read == 30.0
    assert timeout.write == 30.0
    assert timeout.pool == 5.0

    custom = TimeoutConfig(connect=1.0, read=2.0, write=3.0, pool=4.0)
    t2 = custom.to_httpx()
    assert t2.connect == 1.0
    assert t2.read == 2.0
    assert t2.write == 3.0
    assert t2.pool == 4.0


def test_timeout_config_rejects_non_positive() -> None:
    with pytest.raises(ValidationError):
        TimeoutConfig(connect=0)
    with pytest.raises(ValidationError):
        TimeoutConfig(read=-1.0)


def test_parse_retry_after_negative_max_wait_clamped() -> None:
    assert parse_retry_after("30", max_wait=-5) == 0.0


def test_parse_retry_after_naive_http_date_and_naive_now() -> None:
    """Naive HTTP-date and naive now are treated as UTC."""
    target = datetime(2026, 7, 25, 12, 0, 42, tzinfo=UTC)
    header = format_datetime(target, usegmt=True)
    now_naive = datetime(2026, 7, 25, 12, 0, 0)  # naive → treated as UTC
    wait = parse_retry_after(header, now=now_naive)
    assert wait is not None
    assert wait == pytest.approx(42.0, abs=0.01)

    # Aware non-UTC now is converted before subtraction.
    now_offset = datetime(2026, 7, 25, 8, 0, 0, tzinfo=timezone(timedelta(hours=-4)))
    wait2 = parse_retry_after(header, now=now_offset)
    assert wait2 is not None
    assert wait2 == pytest.approx(42.0, abs=0.01)


def test_full_jitter_edge_clamps() -> None:
    rng = random.Random(0)
    # attempt_number < 1 clamps to 1.
    wait = full_jitter_seconds(0, initial=1.0, max_wait=4.0, exp_base=2.0, rng=rng)
    assert 0.0 <= wait <= 1.0

    # Negative initial / max_wait → 0 high → 0.0
    assert full_jitter_seconds(3, initial=-1.0, max_wait=8.0, exp_base=2.0, rng=rng) == 0.0
    assert full_jitter_seconds(3, initial=1.0, max_wait=-1.0, exp_base=2.0, rng=rng) == 0.0

    # exp_base < 1 clamps to 1.0 → high stays at initial.
    rng2 = random.Random(1)
    for _ in range(20):
        w = full_jitter_seconds(5, initial=2.0, max_wait=10.0, exp_base=0.5, rng=rng2)
        assert 0.0 <= w <= 2.0


def test_full_jitter_overflow_uses_max_wait() -> None:
    rng = random.Random(3)
    # Enormous exponent can OverflowError; should fall back to max_wait bound.
    wait = full_jitter_seconds(
        10_000,
        initial=1e300,
        max_wait=5.0,
        exp_base=10.0,
        rng=rng,
    )
    assert 0.0 <= wait <= 5.0


def test_full_jitter_without_rng_uses_module_random() -> None:
    wait = full_jitter_seconds(1, initial=0.5, max_wait=1.0, exp_base=2.0, rng=None)
    assert 0.0 <= wait <= 0.5


def test_wait_full_jitter_default_rng() -> None:
    wait = wait_full_jitter(initial=0.5, max=2.0, exp_base=2.0)
    state = SimpleNamespace(attempt_number=1)
    value = wait(state)  # ty: ignore[invalid-argument-type]
    assert 0.0 <= value <= 0.5
