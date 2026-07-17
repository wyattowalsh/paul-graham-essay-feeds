"""Unit tests for extract.py."""

from __future__ import annotations

import pytest

from paul_graham_essay_feeds.extract import extract_essays
from paul_graham_essay_feeds.model import FeedError
from tests.html_samples import MARKER, synthetic_index_html


def test_extract_meets_floor(sample_html: str) -> None:
    essays = extract_essays(sample_html, min_items=233)
    assert len(essays) == 233
    assert essays[0].position == 1
    assert essays[0].title == "Essay 0"
    assert essays[0].url.endswith("/essay-0.html")
    assert essays[0].is_permalink is True


def test_extract_protected_chapters(sample_html: str) -> None:
    essays = extract_essays(sample_html, min_items=233)
    urls = {e.url for e in essays}
    assert "https://sep.turbifycdn.com/ty/cdn/paulgraham/acl1.txt" in urls
    assert "https://sep.turbifycdn.com/ty/cdn/paulgraham/acl2.txt" in urls
    turbify = [e for e in essays if "turbify" in e.url]
    assert all(not e.is_permalink for e in turbify)
    assert all(e.stable_id.startswith("urn:uuid:") for e in turbify)
    # Competitor bug: double-prefixed turbify URLs under paulgraham.com
    assert not any("paulgraham.com/https://" in e.url for e in essays)
    assert all(e.url.startswith("https://") for e in essays)


def test_extract_too_few_items() -> None:
    with pytest.raises(FeedError, match="Only"):
        extract_essays("<a href='x.html'>only one</a>", min_items=10)


def test_extract_missing_protected_chapters() -> None:
    rows = [f'<img src="{MARKER}"><a href="e{i}.html">E{i}</a>' for i in range(233)]
    with pytest.raises(FeedError, match="protected"):
        extract_essays("".join(rows), min_items=233)


def test_extract_fallback_when_markers_sparse() -> None:
    html = synthetic_index_html(essay_count=231)
    plain = html.replace(
        f'src="{MARKER}"',
        'src="other.gif"',
    )
    essays = extract_essays(plain, min_items=233)
    assert len(essays) == 233


def test_extract_dedupe_last_occurrence() -> None:
    # Fallback path with duplicate essay links keeps last title.
    html = (
        '<a href="dup.html">First</a>'
        '<a href="dup.html">Second</a>'
        '<a href="https://sep.turbifycdn.com/ty/cdn/paulgraham/acl1.txt">C1</a>'
        '<a href="https://sep.turbifycdn.com/ty/cdn/paulgraham/acl2.txt">C2</a>'
        + "".join(f'<a href="e{i}.html">E{i}</a>' for i in range(5))
    )
    essays = extract_essays(html, min_items=8)
    dups = [e for e in essays if e.url.endswith("/dup.html")]
    assert len(dups) == 1
    assert dups[0].title == "Second"


def test_extract_skips_empty_title_and_bad_href() -> None:
    # Marker rows with empty title and invalid host should be skipped; still need
    # enough good essays + protected chapters for min_items=3.
    chunks = [
        f'<img src="{MARKER}"><a href="https://evil.example/x.html">Evil</a>',
        f'<img src="{MARKER}"><a href="good.html"></a>',  # empty title
        f'<img src="{MARKER}"><a>no href</a>',
        f'<img src="{MARKER}"><a href="articles.html">Essays</a>',  # excluded path
        f'<img src="{MARKER}"><a href="a.html">A</a>',
        f'<img src="{MARKER}"><a href="b.html">B</a>',
        f'<img src="{MARKER}"><a href="c.html">C</a>',
        f'<img src="{MARKER}">'
        '<a href="https://sep.turbifycdn.com/ty/cdn/paulgraham/acl1.txt">C1</a>',
        f'<img src="{MARKER}">'
        '<a href="https://sep.turbifycdn.com/ty/cdn/paulgraham/acl2.txt">C2</a>',
    ]
    essays = extract_essays("".join(chunks), min_items=5)
    assert len(essays) == 5
    assert all("evil" not in e.url for e in essays)
