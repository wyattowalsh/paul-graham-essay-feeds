"""F-016: HEAD Content-Length must not be treated as body download budget."""

from __future__ import annotations

import httpx
import pytest
import respx

from paul_graham_essay_feeds.fetch import hop_safe_request
from paul_graham_essay_feeds.model import ALLOWED_HOSTS, FeedError


@pytest.mark.characterization
@pytest.mark.xfail(
    strict=True,
    reason="F-016: HEAD still shares GET body-size rejection path in hop_safe_request",
)
@respx.mock
def test_head_with_large_content_length_is_allowed() -> None:
    """HEAD advertising a large representation must not raise solely on Content-Length."""
    respx.head("https://paulgraham.com/big.html").mock(
        return_value=httpx.Response(
            200,
            headers={"content-length": str(50_000_000), "content-type": "text/html"},
        )
    )
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
        try:
            hop_safe_request(
                client,
                "HEAD",
                "https://paulgraham.com/big.html",
                allowed_hosts=ALLOWED_HOSTS,
                max_bytes=1024,
            )
            ok = True
            err = None
        except FeedError as exc:
            ok = False
            err = str(exc)
    assert ok, f"HEAD large Content-Length should be allowed, got: {err}"
