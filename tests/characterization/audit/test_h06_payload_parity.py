"""RES-H06: cross-format title/url/summary payload parity."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from paul_graham_essay_feeds.feeds import render_atom, render_json, render_rss
from paul_graham_essay_feeds.models import FeedEntrySnapshot, FeedSnapshot
from paul_graham_essay_feeds.verify import (
    SUMMARY_ORDER_MISMATCH,
    TITLE_ORDER_MISMATCH,
    URL_ORDER_MISMATCH,
    verify_feed_bytes,
)

T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)


def _triple() -> tuple[bytes, bytes, bytes]:
    snap = FeedSnapshot(
        logical_updated_at=T0,
        generator="pg-essay-feeds/test",
        items=[
            FeedEntrySnapshot(
                id="https://paulgraham.com/a.html",
                url="https://paulgraham.com/a.html",
                title="A",
                summary="Short summary for essay A.",
                observed_updated_at=T0,
            ),
            FeedEntrySnapshot(
                id="https://paulgraham.com/b.html",
                url="https://paulgraham.com/b.html",
                title="B",
                summary="Short summary for essay B.",
                observed_updated_at=T0,
            ),
        ],
    )
    return render_rss(snap), render_atom(snap), render_json(snap)


def _codes(report) -> set[str]:  # type: ignore[no-untyped-def]
    return {v.code for v in report.violations}


def test_title_diverge_across_formats() -> None:
    rss, atom, jf = _triple()
    payload = json.loads(jf.decode("utf-8"))
    payload["items"][0]["title"] = "DIFFERENT"
    jf = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
    report = verify_feed_bytes(rss=rss, atom=atom, json_feed=jf, min_items=2)
    assert report.ok is False
    assert TITLE_ORDER_MISMATCH in _codes(report)


def test_url_diverge_across_formats() -> None:
    rss, atom, jf = _triple()
    payload = json.loads(jf.decode("utf-8"))
    payload["items"][0]["url"] = "https://paulgraham.com/other.html"
    jf = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
    report = verify_feed_bytes(rss=rss, atom=atom, json_feed=jf, min_items=2)
    assert report.ok is False
    assert URL_ORDER_MISMATCH in _codes(report)


def test_summary_diverge_across_formats() -> None:
    rss, atom, jf = _triple()
    payload = json.loads(jf.decode("utf-8"))
    payload["items"][0]["summary"] = "Totally different summary text."
    payload["items"][0]["content_text"] = payload["items"][0]["summary"]
    jf = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
    report = verify_feed_bytes(rss=rss, atom=atom, json_feed=jf, min_items=2)
    assert report.ok is False
    assert SUMMARY_ORDER_MISMATCH in _codes(report)


def test_happy_payload_parity() -> None:
    rss, atom, jf = _triple()
    report = verify_feed_bytes(rss=rss, atom=atom, json_feed=jf, min_items=2)
    assert report.ok is True
