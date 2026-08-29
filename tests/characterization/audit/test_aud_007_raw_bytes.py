"""AUD-007: raw_sha256 / bytes_received are wire bytes, not content-decoded."""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest
import respx

from paul_graham_essay_feeds.http import ResultKind, get_with_evidence, hop_safe_request
from paul_graham_essay_feeds.models import ALLOWED_HOSTS, FeedError

HTTP_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "http"


@pytest.mark.characterization
@respx.mock
def test_gzip_wire_vs_decoded_hashes() -> None:
    plain = (HTTP_FIXTURES / "plain.html").read_bytes()
    wire = (HTTP_FIXTURES / "plain.html.gz").read_bytes()
    assert hashlib.sha256(plain).hexdigest() != hashlib.sha256(wire).hexdigest()

    respx.get("https://paulgraham.com/gz.html").mock(
        return_value=httpx.Response(
            200,
            content=wire,
            headers={
                "Content-Encoding": "gzip",
                "Content-Type": "text/html; charset=utf-8",
                "Content-Length": str(len(wire)),
            },
        )
    )
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
        result = get_with_evidence(
            client,
            "https://paulgraham.com/gz.html",
            allowed_hosts=ALLOWED_HOSTS,
            max_bytes=1024,
        )
    ev = result.evidence
    assert ev.result_kind is ResultKind.FETCHED
    assert result.body == plain
    assert result.raw_body == wire
    assert ev.raw_sha256 == hashlib.sha256(wire).hexdigest()
    assert ev.decoded_sha256 == hashlib.sha256(plain).hexdigest()
    assert ev.bytes_received == len(wire)
    assert ev.decoded_bytes_received == len(plain)
    assert ev.content_length_header == len(wire)


@pytest.mark.characterization
@respx.mock
def test_brotli_wire_vs_decoded_when_available() -> None:
    brotli = pytest.importorskip("brotli")
    plain = b"<html>pg-essay-feeds brotli fixture</html>\n"
    wire = brotli.compress(plain)
    respx.get("https://paulgraham.com/br.html").mock(
        return_value=httpx.Response(
            200,
            content=wire,
            headers={"Content-Encoding": "br", "Content-Type": "text/html"},
        )
    )
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
        result = get_with_evidence(
            client,
            "https://paulgraham.com/br.html",
            allowed_hosts=ALLOWED_HOSTS,
            max_bytes=1024,
        )
    ev = result.evidence
    assert result.body == plain
    assert ev.raw_sha256 == hashlib.sha256(wire).hexdigest()
    assert ev.decoded_sha256 == hashlib.sha256(plain).hexdigest()
    assert ev.bytes_received == len(wire)
    assert ev.decoded_bytes_received == len(plain)


@pytest.mark.characterization
@respx.mock
def test_head_large_content_length_still_allowed_f016() -> None:
    """F-016: HEAD must not treat Content-Length as a downloaded-body budget."""
    respx.head("https://paulgraham.com/big.html").mock(
        return_value=httpx.Response(
            200,
            headers={"content-length": str(50_000_000), "content-type": "text/html"},
        )
    )
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
        response = hop_safe_request(
            client,
            "HEAD",
            "https://paulgraham.com/big.html",
            allowed_hosts=ALLOWED_HOSTS,
            max_bytes=1024,
        )
    assert response.status_code == 200


@pytest.mark.characterization
@respx.mock
def test_gzip_oversize_uses_wire_budget() -> None:
    wire = (HTTP_FIXTURES / "plain.html.gz").read_bytes()
    respx.get("https://paulgraham.com/gz.html").mock(
        return_value=httpx.Response(
            200,
            content=wire,
            headers={"Content-Encoding": "gzip", "Content-Length": str(len(wire))},
        )
    )
    with (
        httpx.Client(trust_env=False, follow_redirects=False) as client,
        pytest.raises(FeedError, match="over"),
    ):
        get_with_evidence(
            client,
            "https://paulgraham.com/gz.html",
            allowed_hosts=ALLOWED_HOSTS,
            max_bytes=len(wire) - 1,
        )
