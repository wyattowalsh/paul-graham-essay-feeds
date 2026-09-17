"""RV-C-001: HEAD must close without buffering a misbehaving entity body."""

from __future__ import annotations

import httpx
import pytest

from paul_graham_essay_feeds.http import (
    ResultKind,
    head_with_evidence,
    hop_safe_request,
)
from paul_graham_essay_feeds.models import ALLOWED_HOSTS

_HEAD_ENTITY = b"x" * 200_000


class _CountingByteStream(httpx.SyncByteStream):
    """Yields *payload* in chunks and records how many bytes were pulled."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self.bytes_yielded = 0

    def __iter__(self):
        step = 8192
        for start in range(0, len(self._payload), step):
            piece = self._payload[start : start + step]
            self.bytes_yielded += len(piece)
            yield piece


class _HeadEntityTransport(httpx.BaseTransport):
    """Offers a large entity only if the client reads the stream."""

    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.stream: _CountingByteStream | None = None

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.stream = _CountingByteStream(self.payload)
        return httpx.Response(
            200,
            headers={
                "Content-Type": "text/html",
                "Content-Length": str(len(self.payload)),
            },
            stream=self.stream,
            request=request,
        )


@pytest.mark.characterization
def test_hop_safe_request_head_does_not_read_entity() -> None:
    transport = _HeadEntityTransport(_HEAD_ENTITY)
    with httpx.Client(
        transport=transport,
        trust_env=False,
        follow_redirects=False,
    ) as client:
        response = hop_safe_request(
            client,
            "HEAD",
            "https://paulgraham.com/big.html",
            allowed_hosts=ALLOWED_HOSTS,
            max_bytes=1024,
        )
    assert response.status_code == 200
    assert response.content == b""
    assert transport.stream is not None
    assert transport.stream.bytes_yielded == 0


@pytest.mark.characterization
def test_head_with_evidence_does_not_read_entity() -> None:
    transport = _HeadEntityTransport(_HEAD_ENTITY)
    with httpx.Client(
        transport=transport,
        trust_env=False,
        follow_redirects=False,
    ) as client:
        result = head_with_evidence(
            client,
            "https://paulgraham.com/big.html",
            allowed_hosts=ALLOWED_HOSTS,
            max_bytes=1024,
        )
    assert result.evidence.result_kind is ResultKind.FETCHED
    assert result.body == b""
    assert result.raw_body == b""
    assert result.evidence.bytes_received == 0
    assert result.evidence.decoded_bytes_received == 0
    assert result.evidence.content_length_header == len(_HEAD_ENTITY)
    assert result.response is not None
    assert result.response.content == b""
    assert transport.stream is not None
    assert transport.stream.bytes_yielded == 0
