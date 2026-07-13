"""Extraction and URL normalization tests."""

from __future__ import annotations

import pytest

from paul_graham_essay_feeds.domain import SOURCE_URL, FeedError, make_stable_id
from paul_graham_essay_feeds.extract import extract_items


def test_marker_extraction_and_url_normalization() -> None:
    rows = "".join(
        f'<img src="https://s.turbifycdn.com/aah/paulgraham/the-reddits-2.gif">'
        f'<a href="essay-{index}.html">Essay {index}</a>'
        for index in range(1, 234)
    )
    rows += (
        '<img src="https://s.turbifycdn.com/aah/paulgraham/the-reddits-2.gif">'
        '<a href="https://sep.turbifycdn.com/ty/cdn/paulgraham/'
        'acl1.txt?t=123&amp;">Chapter 1 of Ansi Common Lisp</a>'
        '<img src="https://s.turbifycdn.com/aah/paulgraham/the-reddits-2.gif">'
        '<a href="https://sep.turbifycdn.com/ty/cdn/paulgraham/'
        'acl2.txt?t=123&amp;">Chapter 2 of Ansi Common Lisp</a>'
    )
    result = extract_items(rows, base_url=SOURCE_URL, min_items=233)
    assert result.mode == "essay-row-marker"
    assert len(result.items) == 235
    assert result.items[-2].url == "https://sep.turbifycdn.com/ty/cdn/paulgraham/acl1.txt?t=123"
    assert not result.items[-2].is_permalink
    sid, _ = make_stable_id("https://sep.turbifycdn.com/ty/cdn/paulgraham/acl1.txt?t=999")
    assert result.items[-2].stable_id == sid


def test_fallback_keeps_last_duplicate_occurrence() -> None:
    recommendations = (
        '<a href="greatwork.html">How to Do Great Work</a>'
        '<a href="kids.html">Having Kids</a>'
        '<a href="selfindulgence.html">How to Lose Time and Money</a>'
    )
    main = recommendations
    for index in range(230):
        main += f'<a href="item-{index}.html">Item {index}</a>'
    main += (
        '<a href="https://sep.turbifycdn.com/ty/cdn/paulgraham/'
        'acl1.txt?t=1">Chapter 1 of Ansi Common Lisp</a>'
        '<a href="https://sep.turbifycdn.com/ty/cdn/paulgraham/'
        'acl2.txt?t=1">Chapter 2 of Ansi Common Lisp</a>'
    )
    result = extract_items(
        recommendations + main,
        base_url=SOURCE_URL,
        min_items=233,
    )
    assert result.mode == "filtered-anchor-fallback"
    assert result.duplicate_count == 3
    assert result.items[0].title == "How to Do Great Work"


def test_invalid_xml_controls_are_removed() -> None:
    from paul_graham_essay_feeds.domain import normalize_text

    assert normalize_text("A\x01  B") == "A B"


def test_legacy_double_prefix_is_rejected() -> None:
    malformed = (
        '<img src="https://s.turbifycdn.com/aah/paulgraham/'
        'the-reddits-2.gif">'
        '<a href="http://www.paulgraham.com/https://sep.turbifycdn.com/'
        'ty/cdn/paulgraham/acl1.txt">Bad</a>'
    )
    with pytest.raises(FeedError):
        extract_items(malformed, base_url=SOURCE_URL, min_items=1)
