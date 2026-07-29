"""Synthetic essay-index HTML for offline tests and CI smoke."""

from __future__ import annotations

from paul_graham_essay_feeds.models import MIN_ITEMS, PROTECTED_PATHS

MARKER = "https://s.turbifycdn.com/aah/paulgraham/the-reddits-2.gif"

# Regular essay rows only; PROTECTED_PATHS chapters are appended separately.
_DEFAULT_ESSAY_COUNT = MIN_ITEMS - len(PROTECTED_PATHS)


def synthetic_index_html(*, essay_count: int = _DEFAULT_ESSAY_COUNT) -> str:
    """Build minimal essay-row HTML (markers + essays + protected Turbify chapters)."""
    rows: list[str] = []
    for index in range(essay_count):
        rows.append(f'<img src="{MARKER}"><a href="essay-{index}.html">Essay {index}</a>')
    rows.append(
        f'<img src="{MARKER}">'
        '<a href="https://sep.turbifycdn.com/ty/cdn/paulgraham/acl1.txt?t=1">'
        "Chapter 1 of Ansi Common Lisp</a>"
    )
    rows.append(
        f'<img src="{MARKER}">'
        '<a href="https://sep.turbifycdn.com/ty/cdn/paulgraham/acl2.txt?t=1">'
        "Chapter 2 of Ansi Common Lisp</a>"
    )
    return "".join(rows)
