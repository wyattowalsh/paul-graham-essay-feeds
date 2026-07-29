"""Unit tests for Tenacity retry helpers."""

from __future__ import annotations

import httpx
import pytest

from paul_graham_essay_feeds.http import is_retryable_exception, run_with_retry
from paul_graham_essay_feeds.models import FeedError


@pytest.fixture(autouse=True)
def _no_tenacity_sleep(monkeypatch: pytest.MonkeyPatch):
    """Zero wall-clock waits so retry paths stay fast and deterministic.

    Tenacity binds ``nap.sleep`` as a default arg on ``Retrying``, so patching
    ``tenacity.nap.sleep`` alone does not affect already-constructed controllers.
    Force ``wait_none`` and a recording no-op ``sleep`` after construction.
    """
    from tenacity import wait_none

    from paul_graham_essay_feeds import http as http_mod

    original = http_mod.retrying
    sleep_calls: list[float] = []

    def _fast_retrying(*, attempts: int, reraise: bool = True):
        controller = original(attempts=attempts, reraise=reraise)
        controller.wait = wait_none()

        def _sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        controller.sleep = _sleep
        return controller

    monkeypatch.setattr(http_mod, "retrying", _fast_retrying)
    yield sleep_calls


def test_feed_error_not_retryable() -> None:
    assert is_retryable_exception(FeedError("nope")) is False


def test_timeout_is_retryable() -> None:
    assert is_retryable_exception(httpx.ConnectTimeout("t")) is True
    assert is_retryable_exception(httpx.ReadError("r")) is True
    assert is_retryable_exception(OSError("os blip")) is True


def test_http_status_retryable_matrix() -> None:
    req = httpx.Request("GET", "https://paulgraham.com/")
    for code in (408, 425, 429, 500, 502, 503, 504):
        resp = httpx.Response(code, request=req)
        exc = httpx.HTTPStatusError("x", request=req, response=resp)
        assert is_retryable_exception(exc) is True
    for code in (400, 403, 404, 501):
        resp = httpx.Response(code, request=req)
        exc = httpx.HTTPStatusError("x", request=req, response=resp)
        assert is_retryable_exception(exc) is False


def test_run_with_retry_succeeds_after_transient(
    _no_tenacity_sleep: list[float],
) -> None:
    calls = {"n": 0}

    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("blip")
        return "ok"

    assert run_with_retry(flaky, attempts=4, what="probe") == "ok"
    assert calls["n"] == 3
    # Sleeps were recorded via the no-op hook (no wall-clock wait).
    assert len(_no_tenacity_sleep) == 2
    assert all(isinstance(s, (int, float)) for s in _no_tenacity_sleep)


def test_run_with_retry_does_not_retry_feed_error() -> None:
    calls = {"n": 0}

    def permanent() -> str:
        calls["n"] += 1
        raise FeedError("hard fail")

    with pytest.raises(FeedError, match="hard fail"):
        run_with_retry(permanent, attempts=5, what="probe")
    assert calls["n"] == 1


def test_run_with_retry_exhausts(_no_tenacity_sleep: list[float]) -> None:
    def always() -> str:
        raise httpx.ConnectError("down")

    with pytest.raises(FeedError, match="after 3"):
        run_with_retry(always, attempts=3, what="fetch")
    assert len(_no_tenacity_sleep) == 2


def test_run_with_retry_exhausts_http_status() -> None:
    req = httpx.Request("GET", "https://paulgraham.com/")
    resp = httpx.Response(503, request=req)

    def always() -> str:
        raise httpx.HTTPStatusError("busy", request=req, response=resp)

    with pytest.raises(FeedError, match=r"HTTP 503.*after 2"):
        run_with_retry(always, attempts=2, what="fetch https://paulgraham.com/")
