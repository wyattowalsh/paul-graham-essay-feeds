"""C-03 / M-01: transport failures retry; Retry-After is honored in wait."""

from __future__ import annotations

import httpx
import pytest

from paul_graham_essay_feeds.http import (
    is_retryable_exception,
    parse_retry_after,
    run_with_retry,
)


def test_c03_transport_error_is_retryable() -> None:
    assert is_retryable_exception(httpx.TransportError("blip")) is True
    assert is_retryable_exception(httpx.ConnectError("c")) is True


def test_c03_run_with_retry_retries_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tenacity import wait_none

    from paul_graham_essay_feeds import http as http_mod

    original = http_mod.retrying

    def _fast(*, attempts: int, reraise: bool = True):
        c = original(attempts=attempts, reraise=reraise)
        c.wait = wait_none()
        c.sleep = lambda _s: None
        return c

    monkeypatch.setattr(http_mod, "retrying", _fast)
    n = {"c": 0}

    def flaky() -> str:
        n["c"] += 1
        if n["c"] < 3:
            raise httpx.TransportError("temp")
        return "ok"

    assert run_with_retry(flaky, attempts=4, what="x") == "ok"
    assert n["c"] == 3


def test_m01_retry_after_parsed_and_capped() -> None:
    assert parse_retry_after("30") == 30.0
    assert parse_retry_after("99999") == 120.0  # DEFAULT_MAX_RETRY_AFTER
