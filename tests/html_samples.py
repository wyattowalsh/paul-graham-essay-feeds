"""Synthetic essay-index HTML for offline tests and CI smoke."""

from __future__ import annotations

MARKER = "https://s.turbifycdn.com/aah/paulgraham/the-reddits-2.gif"


def synthetic_index_html(*, essay_count: int = 231) -> str:
    """Build minimal essay-row HTML (markers + essays + protected Turbify chapters)."""
    rows: list[str] = []
    for index in range(essay_count):
        rows.append(
            f'<img src="{MARKER}">'
            f'<a href="essay-{index}.html">Essay {index}</a>'
        )
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
