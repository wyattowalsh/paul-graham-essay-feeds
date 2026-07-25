"""Validate final included essay links (structural + optional live HEAD)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlsplit

import httpx
from loguru import logger

from paul_graham_essay_feeds.fetch import hop_safe_get, hop_safe_request, run_with_retry
from paul_graham_essay_feeds.model import (
    ALLOWED_HOSTS,
    MAX_BYTES,
    Essay,
    FeedError,
    user_agent,
    validate_essay_link,
)
from paul_graham_essay_feeds.presentation import NULL_REPORTER, OutputPolicy, ProgressReporter

_USER_AGENT = user_agent(" link-check")


def validate_essays_structural(
    essays: list[Essay],
    *,
    reporter: ProgressReporter | None = None,
) -> None:
    """Always-on validation for every included link."""
    progress = reporter or NULL_REPORTER
    if len(essays) < 20:
        iterable = essays
    else:
        iterable = progress.track(essays, desc="Validate links", unit="url")
    for essay in iterable:
        validate_essay_link(essay)
    logger.info("Structural link validation OK ({} urls)", len(essays))


def _probe_once(client: httpx.Client, essay: Essay, *, max_bytes: int) -> None:
    """Single probe attempt; raises httpx errors for tenacity."""
    # allow_loopback=None → hop_safe derives from start URL (essay.url) host.
    response = hop_safe_request(
        client,
        "HEAD",
        essay.url,
        allowed_hosts=ALLOWED_HOSTS,
        max_bytes=max_bytes,
        allow_loopback=None,
    )
    if response.status_code in {405, 501}:
        response = hop_safe_get(
            client,
            essay.url,
            allowed_hosts=ALLOWED_HOSTS,
            max_bytes=max_bytes,
            allow_loopback=None,
        )
    if response.status_code >= 400:
        # Non-retryable client errors stay FeedError; 5xx becomes HTTPStatusError-like.
        if response.status_code in {408, 425, 429, 500, 502, 503, 504}:
            response.raise_for_status()
        raise FeedError(f"{essay.url} → HTTP {response.status_code}")
    host = urlsplit(str(response.url)).hostname or ""
    host_l = host.lower()
    if host_l == "www.paulgraham.com":
        host_l = "paulgraham.com"
    if host_l not in ALLOWED_HOSTS:
        raise FeedError(f"{essay.url} redirected to disallowed host {host!r}")


def _probe_one(
    client: httpx.Client,
    essay: Essay,
    *,
    attempts: int,
    max_bytes: int,
) -> str | None:
    """Return error message or None if OK (retries transient failures)."""
    try:
        run_with_retry(
            lambda: _probe_once(client, essay, max_bytes=max_bytes),
            attempts=attempts,
            what=f"probe {essay.url}",
        )
    except FeedError as exc:
        return str(exc)
    except httpx.HTTPError as exc:
        return f"{essay.url} → {exc}"
    return None


def validate_essays_live(
    essays: list[Essay],
    *,
    timeout: float = 10.0,
    workers: int = 4,
    retries: int = 2,
    max_bytes: int = MAX_BYTES,
    quiet: bool = False,
    reporter: ProgressReporter | None = None,
) -> None:
    """Optional live probe of each essay URL (slow; Tenacity per-URL)."""
    errors: list[str] = []
    attempts = max(1, retries + 1)
    progress = reporter or ProgressReporter(OutputPolicy(quiet=quiet))
    with (
        httpx.Client(
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            trust_env=False,
            headers={"User-Agent": _USER_AGENT},
        ) as client,
        ThreadPoolExecutor(max_workers=workers) as pool,
    ):
        futures = {
            pool.submit(
                _probe_one,
                client,
                e,
                attempts=attempts,
                max_bytes=max_bytes,
            ): e
            for e in essays
        }
        for fut in progress.track(
            as_completed(futures),
            total=len(futures),
            desc="Probe links",
            unit="url",
        ):
            err = fut.result()
            if err:
                errors.append(err)
    if errors:
        preview = "\n  ".join(errors[:10])
        more = f"\n  … +{len(errors) - 10} more" if len(errors) > 10 else ""
        raise FeedError(f"{len(errors)} link probe failure(s):\n  {preview}{more}")
    logger.info("Live link probes OK ({} urls)", len(essays))
