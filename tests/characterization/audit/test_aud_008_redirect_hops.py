"""AUD-008: hop policy rejects userinfo, fragments, non-443 HTTPS ports, encoded hosts."""

from __future__ import annotations

import httpx
import pytest
import respx

from paul_graham_essay_feeds.http import _assert_hop_allowed, hop_safe_get, request_with_evidence
from paul_graham_essay_feeds.models import FeedError

ALLOWED = frozenset({"paulgraham.com"})


@pytest.mark.characterization
@pytest.mark.parametrize(
    ("url", "match"),
    [
        ("https://user:pass@paulgraham.com/a.html", "Userinfo"),
        ("https://user@paulgraham.com/a.html", "Userinfo"),
        ("https://@paulgraham.com/a.html", "Userinfo"),
        ("https://paulgraham.com/a.html#frag", "Fragment"),
        ("https://paulgraham.com:444/a.html", "Port not allowed"),
        ("https://paulgraham.com%2eevil.com/a.html", "Encoded host"),
    ],
)
def test_assert_hop_rejects_tricks(url: str, match: str) -> None:
    with pytest.raises(FeedError, match=match):
        _assert_hop_allowed(url, ALLOWED, allow_loopback=False)


@pytest.mark.characterization
def test_loopback_http_any_port_only_when_allowed() -> None:
    _assert_hop_allowed(
        "http://127.0.0.1:9999/x",
        ALLOWED,
        allow_loopback=True,
    )
    _assert_hop_allowed(
        "http://localhost:8/x",
        ALLOWED,
        allow_loopback=True,
    )
    with pytest.raises(FeedError, match="not allowed"):
        _assert_hop_allowed("http://127.0.0.1:9999/x", ALLOWED, allow_loopback=False)


@pytest.mark.characterization
def test_https_loopback_port_must_be_443() -> None:
    _assert_hop_allowed("https://127.0.0.1/x", ALLOWED, allow_loopback=True)
    _assert_hop_allowed("https://127.0.0.1:443/x", ALLOWED, allow_loopback=True)
    with pytest.raises(FeedError, match="Port not allowed"):
        _assert_hop_allowed("https://127.0.0.1:8443/x", ALLOWED, allow_loopback=True)


@pytest.mark.characterization
def test_idna_and_trailing_dot_normalize() -> None:
    _assert_hop_allowed("https://www.paulgraham.com./a.html", ALLOWED, allow_loopback=False)
    _assert_hop_allowed("https://PAULGRAHAM.COM/a.html", ALLOWED, allow_loopback=False)


@pytest.mark.characterization
@respx.mock
def test_location_userinfo_rejected_before_follow() -> None:
    start = respx.get("https://paulgraham.com/a.html").mock(
        return_value=httpx.Response(
            302,
            headers={"Location": "https://u:p@paulgraham.com/secret"},
        )
    )
    evil = respx.get("https://u:p@paulgraham.com/secret").mock(
        return_value=httpx.Response(200, text="nope")
    )
    with (
        httpx.Client(follow_redirects=False, trust_env=False) as client,
        pytest.raises(FeedError, match="Userinfo"),
    ):
        hop_safe_get(
            client,
            "https://paulgraham.com/a.html",
            allowed_hosts=ALLOWED,
            max_bytes=1024,
        )
    assert start.called
    assert not evil.called


@pytest.mark.characterization
@respx.mock
def test_protocol_relative_location_revalidated() -> None:
    respx.get("https://paulgraham.com/a.html").mock(
        return_value=httpx.Response(302, headers={"Location": "//evil.example/x"})
    )
    with (
        httpx.Client(follow_redirects=False, trust_env=False) as client,
        pytest.raises(FeedError, match="not allowed"),
    ):
        request_with_evidence(
            client,
            "GET",
            "https://paulgraham.com/a.html",
            allowed_hosts=ALLOWED,
            max_bytes=1024,
        )


@pytest.mark.characterization
@respx.mock
def test_protocol_relative_port_444_rejected() -> None:
    respx.get("https://paulgraham.com/a.html").mock(
        return_value=httpx.Response(302, headers={"Location": "//paulgraham.com:444/b"})
    )
    with (
        httpx.Client(follow_redirects=False, trust_env=False) as client,
        pytest.raises(FeedError, match="Port not allowed"),
    ):
        hop_safe_get(
            client,
            "https://paulgraham.com/a.html",
            allowed_hosts=ALLOWED,
            max_bytes=1024,
        )


@pytest.mark.characterization
@respx.mock
def test_fragment_on_location_rejected() -> None:
    respx.get("https://paulgraham.com/a.html").mock(
        return_value=httpx.Response(302, headers={"Location": "https://paulgraham.com/b.html#x"})
    )
    with (
        httpx.Client(follow_redirects=False, trust_env=False) as client,
        pytest.raises(FeedError, match="Fragment"),
    ):
        hop_safe_get(
            client,
            "https://paulgraham.com/a.html",
            allowed_hosts=ALLOWED,
            max_bytes=1024,
        )
