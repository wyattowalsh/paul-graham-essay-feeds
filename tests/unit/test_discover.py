"""Unit tests for discover.py (marker strategy, fail-closed fallback, dups)."""

from __future__ import annotations

from pathlib import Path

import pytest

from paul_graham_essay_feeds.discover import (
    ExtractionReport,
    ExtractionStrategy,
    build_discovery_snapshot,
    discover_essays,
    evaluate_discovery_anomaly,
)
from paul_graham_essay_feeds.models import (
    ABSENCE_QUARANTINE_MIN_REMOVED,
    MIN_ITEMS,
    FeedError,
)
from tests.html_samples import MARKER, synthetic_index_html

UPSTREAM = Path(__file__).resolve().parents[1] / "fixtures" / "upstream"


def _load_upstream(name: str) -> str:
    return (UPSTREAM / name).read_text(encoding="utf-8")


def test_marker_happy_path(sample_html: str) -> None:
    essays, report = discover_essays(sample_html, min_items=MIN_ITEMS)
    assert len(essays) >= MIN_ITEMS
    assert essays[0].position == 1
    assert essays[0].title == "Essay 0"
    assert essays[0].url.endswith("/essay-0.html")
    assert essays[0].is_permalink is True
    assert report.strategy is ExtractionStrategy.MARKER
    assert report.fallback_used is False
    assert report.marked_count >= MIN_ITEMS
    assert report.drift_score == 0.0
    urls = {e.url for e in essays}
    assert "https://sep.turbifycdn.com/ty/cdn/paulgraham/acl1.txt" in urls
    assert "https://sep.turbifycdn.com/ty/cdn/paulgraham/acl2.txt" in urls


def test_sparse_markers_fail_closed_without_fallback() -> None:
    html = synthetic_index_html()
    plain = html.replace(f'src="{MARKER}"', 'src="other.gif"')
    with pytest.raises(FeedError, match="fallback disabled"):
        discover_essays(plain, min_items=MIN_ITEMS, allow_fallback=False)


def test_sparse_markers_with_allow_fallback() -> None:
    html = synthetic_index_html()
    plain = html.replace(f'src="{MARKER}"', 'src="other.gif"')
    essays, report = discover_essays(plain, min_items=MIN_ITEMS, allow_fallback=True)
    assert len(essays) >= MIN_ITEMS
    assert report.fallback_used is True
    assert report.strategy is ExtractionStrategy.FALLBACK
    assert report.marked_count == 0
    assert report.drift_score > 0.0


def test_duplicate_anchors_keep_first() -> None:
    # Two marked rows for the same essay; first title must win (not last-wins).
    html = (
        f'<img src="{MARKER}"><a href="dup.html">First</a>'
        f'<img src="{MARKER}"><a href="dup.html">Second</a>'
        f'<img src="{MARKER}">'
        '<a href="https://sep.turbifycdn.com/ty/cdn/paulgraham/acl1.txt">C1</a>'
        f'<img src="{MARKER}">'
        '<a href="https://sep.turbifycdn.com/ty/cdn/paulgraham/acl2.txt">C2</a>'
        + "".join(f'<img src="{MARKER}"><a href="e{i}.html">E{i}</a>' for i in range(5))
    )
    essays, report = discover_essays(html, min_items=8, allow_fallback=False)
    dups = [e for e in essays if e.url.endswith("/dup.html")]
    assert len(dups) == 1
    assert dups[0].title == "First"
    # Second occurrence recorded as anomaly; permalink stable_id is the URL.
    assert any(d.endswith("/dup.html") for d in report.duplicates)


def test_marker_does_not_leak_across_table_rows() -> None:
    """Marker pending clears when its row/cell closes (anti sticky-leak)."""
    # Marker in row 1 with no essay anchor; nav link in row 2 must not be marked.
    # Real essays in later marked rows still count.
    chunks = [
        "<table>",
        f'<tr><td><img src="{MARKER}"></td></tr>',
        '<tr><td><a href="articles.html">Essays</a></td></tr>',
        f'<tr><td><img src="{MARKER}"><a href="a.html">A</a></td></tr>',
        f'<tr><td><img src="{MARKER}"><a href="b.html">B</a></td></tr>',
        f'<tr><td><img src="{MARKER}"><a href="c.html">C</a></td></tr>',
        f'<tr><td><img src="{MARKER}">'
        '<a href="https://sep.turbifycdn.com/ty/cdn/paulgraham/acl1.txt">C1</a></td></tr>',
        f'<tr><td><img src="{MARKER}">'
        '<a href="https://sep.turbifycdn.com/ty/cdn/paulgraham/acl2.txt">C2</a></td></tr>',
        "</table>",
    ]
    essays, report = discover_essays("".join(chunks), min_items=5)
    assert len(essays) == 5
    assert all(not e.url.endswith("/articles.html") for e in essays)
    assert report.strategy is ExtractionStrategy.MARKER
    assert report.fallback_used is False


def test_missing_protected_chapters_raises() -> None:
    rows = [f'<img src="{MARKER}"><a href="e{i}.html">E{i}</a>' for i in range(MIN_ITEMS)]
    with pytest.raises(FeedError, match="protected"):
        discover_essays("".join(rows), min_items=MIN_ITEMS)


def test_empty_title_and_nav_rejections_recorded() -> None:
    """Empty titles, excluded paths, and bad hosts land in report.rejections."""
    html = (
        f'<img src="{MARKER}"><a href="ok.html">Good Essay</a>'
        f'<img src="{MARKER}"><a href="empty.html"></a>'
        f'<img src="{MARKER}"><a href="articles.html">Essays</a>'
        f'<img src="{MARKER}"><a href="https://evil.example/x">Evil</a>'
        f'<img src="{MARKER}">'
        '<a href="https://sep.turbifycdn.com/ty/cdn/paulgraham/acl1.txt">C1</a>'
        f'<img src="{MARKER}">'
        '<a href="https://sep.turbifycdn.com/ty/cdn/paulgraham/acl2.txt">C2</a>'
        + "".join(f'<img src="{MARKER}"><a href="e{i}.html">E{i}</a>' for i in range(6))
    )
    essays, report = discover_essays(html, min_items=8, allow_fallback=False)
    assert len(essays) >= 8
    joined = " | ".join(report.rejections)
    assert "empty title" in joined
    assert "not a content candidate" in joined
    assert any("Host not allowed" in r for r in report.rejections)

    snap = build_discovery_snapshot(html, min_items=8)
    assert snap.items
    assert snap.candidates
    assert any(not c.accepted for c in snap.candidates)
    assert any(c.rejection_reason == "empty title" for c in snap.candidates)
    assert any(
        c.rejection_reason is not None and "Host not allowed" in c.rejection_reason
        for c in snap.candidates
    )


def test_fallback_still_below_min_items_raises() -> None:
    html = (
        '<a href="only.html">Only One</a>'
        '<a href="https://sep.turbifycdn.com/ty/cdn/paulgraham/acl1.txt">C1</a>'
        '<a href="https://sep.turbifycdn.com/ty/cdn/paulgraham/acl2.txt">C2</a>'
    )
    with pytest.raises(FeedError, match=r"Only .* essays"):
        discover_essays(html, min_items=10, allow_fallback=True)


def test_anchor_without_href_ignored() -> None:
    html = (
        f'<img src="{MARKER}"><a>No href</a>'
        f'<img src="{MARKER}"><a href="a.html">A</a>'
        f'<img src="{MARKER}"><a href="b.html">B</a>'
        f'<img src="{MARKER}"><a href="c.html">C</a>'
        f'<img src="{MARKER}">'
        '<a href="https://sep.turbifycdn.com/ty/cdn/paulgraham/acl1.txt">C1</a>'
        f'<img src="{MARKER}">'
        '<a href="https://sep.turbifycdn.com/ty/cdn/paulgraham/acl2.txt">C2</a>'
        + "".join(f'<img src="{MARKER}"><a href="e{i}.html">E{i}</a>' for i in range(4))
    )
    essays, report = discover_essays(html, min_items=8)
    assert all(e.title != "No href" for e in essays)
    assert report.strategy is ExtractionStrategy.MARKER


def test_marker_cleared_when_scope_left_before_anchor() -> None:
    """Marker in a closed list item does not mark a later out-of-scope anchor."""
    html = (
        f'<ul><li><img src="{MARKER}"></li></ul>'
        '<a href="leak.html">Should Not Mark Alone</a>'
        f'<img src="{MARKER}"><a href="real.html">Real</a>'
        f'<img src="{MARKER}">'
        '<a href="https://sep.turbifycdn.com/ty/cdn/paulgraham/acl1.txt">C1</a>'
        f'<img src="{MARKER}">'
        '<a href="https://sep.turbifycdn.com/ty/cdn/paulgraham/acl2.txt">C2</a>'
        + "".join(f'<img src="{MARKER}"><a href="e{i}.html">E{i}</a>' for i in range(6))
    )
    essays, report = discover_essays(html, min_items=8)
    assert all(not e.url.endswith("/leak.html") for e in essays)
    assert any(e.url.endswith("/real.html") for e in essays)
    assert report.fallback_used is False


def test_drift_score_increases_with_duplicates_and_rejections() -> None:
    html = (
        f'<img src="{MARKER}"><a href="dup.html">First</a>'
        f'<img src="{MARKER}"><a href="dup.html">Second</a>'
        f'<img src="{MARKER}"><a href="empty.html"></a>'
        f'<img src="{MARKER}">'
        '<a href="https://sep.turbifycdn.com/ty/cdn/paulgraham/acl1.txt">C1</a>'
        f'<img src="{MARKER}">'
        '<a href="https://sep.turbifycdn.com/ty/cdn/paulgraham/acl2.txt">C2</a>'
        + "".join(f'<img src="{MARKER}"><a href="e{i}.html">E{i}</a>' for i in range(6))
    )
    _essays, report = discover_essays(html, min_items=8)
    assert report.duplicates
    assert report.rejections
    assert report.drift_score > 0.0


# --- Upstream fixture corpus (P0) -------------------------------------------


def test_fixture_index_marker_basic() -> None:
    essays, report = discover_essays(
        _load_upstream("index-marker-basic.html"),
        min_items=8,
        allow_fallback=False,
    )
    assert len(essays) >= 8
    assert essays[0].title == "Essay 0"
    assert report.strategy is ExtractionStrategy.MARKER
    assert report.fallback_used is False


def test_fixture_index_marker_leak() -> None:
    essays, report = discover_essays(
        _load_upstream("index-marker-leak.html"),
        min_items=7,
        allow_fallback=False,
    )
    assert all(not e.url.endswith("/articles.html") for e in essays)
    assert report.strategy is ExtractionStrategy.MARKER


def test_fixture_index_sparse_fail_closed_and_fallback() -> None:
    html = _load_upstream("index-sparse-fallback.html")
    with pytest.raises(FeedError, match="fallback disabled"):
        discover_essays(html, min_items=8, allow_fallback=False)
    essays, report = discover_essays(html, min_items=8, allow_fallback=True)
    assert len(essays) >= 8
    assert report.fallback_used is True
    assert report.strategy is ExtractionStrategy.FALLBACK


def test_fixture_index_duplicate_anchors() -> None:
    essays, report = discover_essays(
        _load_upstream("index-duplicate-anchors.html"),
        min_items=8,
        allow_fallback=False,
    )
    dups = [e for e in essays if e.url.endswith("/dup.html")]
    assert len(dups) == 1
    assert dups[0].title == "First Title"
    assert any(d.endswith("/dup.html") for d in report.duplicates)


def _anomaly_report() -> ExtractionReport:
    return ExtractionReport(
        strategy=ExtractionStrategy.MARKER,
        fallback_used=False,
        marked_count=20,
        drift_score=0.0,
    )


def test_four_removals_do_not_quarantine_five_ratio_does() -> None:
    """PGF-2026-013: 1-4 omissions are hysteresis; >=5 + ratio still quarantines."""
    prior = {f"https://paulgraham.com/e{i}.html" for i in range(10)}
    omit_four = set(list(prior)[4:])
    assert evaluate_discovery_anomaly(prior, omit_four, report=_anomaly_report()) is None

    omit_five = set(list(prior)[5:])
    reason = evaluate_discovery_anomaly(prior, omit_five, report=_anomaly_report())
    assert reason is not None
    assert "removal" in reason
    assert ABSENCE_QUARANTINE_MIN_REMOVED == 5
