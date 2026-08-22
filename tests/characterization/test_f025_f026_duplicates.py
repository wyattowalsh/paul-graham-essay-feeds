"""F-025 / F-026: first-wins duplicates with anomaly reporting.

Thin characterization aliases of ``tests/unit/test_discover.py``
(``test_duplicate_anchors_keep_first``); no new discovery behavior.
"""

from __future__ import annotations

from paul_graham_essay_feeds.discover import discover_essays
from tests.html_samples import MARKER


def _duplicate_anchor_html() -> str:
    return (
        f'<img src="{MARKER}"><a href="dup.html">First</a>'
        f'<img src="{MARKER}"><a href="dup.html">Second</a>'
        f'<img src="{MARKER}">'
        '<a href="https://sep.turbifycdn.com/ty/cdn/paulgraham/acl1.txt">C1</a>'
        f'<img src="{MARKER}">'
        '<a href="https://sep.turbifycdn.com/ty/cdn/paulgraham/acl2.txt">C2</a>'
        + "".join(f'<img src="{MARKER}"><a href="e{i}.html">E{i}</a>' for i in range(5))
    )


def test_f025_duplicate_anchors_first_wins() -> None:
    """F-025: first structurally valid occurrence wins (not last-wins)."""
    essays, _report = discover_essays(
        _duplicate_anchor_html(),
        min_items=8,
        allow_fallback=False,
    )
    dups = [e for e in essays if e.url.endswith("/dup.html")]
    assert len(dups) == 1
    assert dups[0].title == "First"


def test_f026_duplicate_anomaly_reported() -> None:
    """F-026: later duplicate stable_ids are recorded on the extraction report."""
    _essays, report = discover_essays(
        _duplicate_anchor_html(),
        min_items=8,
        allow_fallback=False,
    )
    assert any(d.endswith("/dup.html") for d in report.duplicates)
