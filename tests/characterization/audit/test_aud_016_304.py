"""AUD-016: HTTP 304 is NOT_MODIFIED only with conditionals and prior material."""

from __future__ import annotations

import httpx
import pytest
import respx

from paul_graham_essay_feeds.enrich import PageEnrichEvidence, enrich_essays
from paul_graham_essay_feeds.http import ResultKind, fetch_index, get_with_evidence
from paul_graham_essay_feeds.models import Essay, FeedError

URL = "https://paulgraham.com/a.html"
INDEX = "https://paulgraham.com/articles.html"


def _essay() -> Essay:
    return Essay(
        position=1,
        title="A",
        url="https://paulgraham.com/earn.html",
        stable_id="https://paulgraham.com/earn.html",
        is_permalink=True,
    )


@pytest.mark.characterization
@respx.mock
def test_valid_304_with_matching_validators() -> None:
    respx.get(URL).mock(return_value=httpx.Response(304, headers={"ETag": '"v1"'}))
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
        result = get_with_evidence(
            client,
            URL,
            allowed_hosts=frozenset({"paulgraham.com"}),
            max_bytes=1024,
            headers={"If-None-Match": '"v1"'},
            prior_etag='"v1"',
        )
    assert result.evidence.result_kind is ResultKind.NOT_MODIFIED
    assert result.body == b""


@pytest.mark.characterization
@respx.mock
def test_valid_304_with_prior_body_hash() -> None:
    respx.get(URL).mock(return_value=httpx.Response(304))
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
        result = get_with_evidence(
            client,
            URL,
            allowed_hosts=frozenset({"paulgraham.com"}),
            max_bytes=1024,
            headers={"If-Modified-Since": "Mon, 01 Jan 2024 00:00:00 GMT"},
            prior_last_modified="Mon, 01 Jan 2024 00:00:00 GMT",
            prior_body_hash="a" * 64,
        )
    assert result.evidence.result_kind is ResultKind.NOT_MODIFIED


@pytest.mark.characterization
@respx.mock
def test_unconditional_304_retries_then_fails() -> None:
    route = respx.get(URL)
    route.side_effect = [httpx.Response(304), httpx.Response(304)]
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
        result = get_with_evidence(
            client,
            URL,
            allowed_hosts=frozenset({"paulgraham.com"}),
            max_bytes=1024,
        )
    assert result.evidence.result_kind is ResultKind.FAILED
    assert result.evidence.status_code == 304
    assert route.call_count == 2


@pytest.mark.characterization
@respx.mock
def test_unconditional_304_retries_then_gets_body() -> None:
    route = respx.get(URL)
    route.side_effect = [
        httpx.Response(304),
        httpx.Response(200, content=b"<html>body</html>"),
    ]
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
        result = get_with_evidence(
            client,
            URL,
            allowed_hosts=frozenset({"paulgraham.com"}),
            max_bytes=1024,
        )
    assert result.evidence.result_kind is ResultKind.FETCHED
    assert result.body == b"<html>body</html>"
    assert route.call_count == 2


@pytest.mark.characterization
@respx.mock
def test_304_without_prior_body_is_not_success() -> None:
    route = respx.get(URL)
    route.side_effect = [httpx.Response(304, headers={"ETag": '"v1"'}), httpx.Response(304)]
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
        result = get_with_evidence(
            client,
            URL,
            allowed_hosts=frozenset({"paulgraham.com"}),
            max_bytes=1024,
            headers={"If-None-Match": '"v1"'},
        )
    assert result.evidence.result_kind is not ResultKind.NOT_MODIFIED
    assert result.evidence.result_kind is ResultKind.FAILED
    assert route.call_count == 2


@pytest.mark.characterization
@respx.mock
def test_mismatched_validators_refetch() -> None:
    route = respx.get(URL)
    route.side_effect = [
        httpx.Response(304, headers={"ETag": '"other"'}),
        httpx.Response(200, content=b"<html>new</html>"),
    ]
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
        result = get_with_evidence(
            client,
            URL,
            allowed_hosts=frozenset({"paulgraham.com"}),
            max_bytes=1024,
            headers={"If-None-Match": '"v1"'},
            prior_etag='"v1"',
        )
    assert result.evidence.result_kind is ResultKind.FETCHED
    assert result.body == b"<html>new</html>"
    assert "if-none-match" not in {k.lower() for k in route.calls[1].request.headers}


@pytest.mark.characterization
@respx.mock
def test_fetch_index_trusts_result_kind_not_bare_304() -> None:
    route = respx.get(INDEX)
    route.side_effect = [httpx.Response(304), httpx.Response(304)]
    with pytest.raises(FeedError, match="304"):
        fetch_index(INDEX, timeout=5.0, retries=0)
    assert route.call_count == 2


@pytest.mark.characterization
@respx.mock
def test_fetch_index_valid_304() -> None:
    respx.get(INDEX).mock(return_value=httpx.Response(304, headers={"ETag": '"i"'}))
    result = fetch_index(INDEX, timeout=5.0, retries=0, etag='"i"')
    assert result.not_modified is True
    assert result.html is None


@pytest.mark.characterization
@respx.mock
def test_enrich_unconditional_304_is_failure_evidence() -> None:
    route = respx.get("https://paulgraham.com/earn.html")
    route.side_effect = [httpx.Response(304), httpx.Response(304)]
    evidence: dict[str, PageEnrichEvidence] = {}
    out = enrich_essays(
        [_essay()],
        workers=1,
        retries=0,
        quiet=True,
        page_evidence_out=evidence,
    )
    ev = evidence[out[0].stable_id]
    assert ev.not_modified is False
    assert ev.ok is False
    assert route.call_count == 2
