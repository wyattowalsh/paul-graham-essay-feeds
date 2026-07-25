"""Pull essay links from the official index HTML."""

from __future__ import annotations

from collections import Counter
from html.parser import HTMLParser
from urllib.parse import urlsplit

from loguru import logger

from paul_graham_essay_feeds.model import (
    MIN_ITEMS,
    PROTECTED_PATHS,
    SOURCE_URL,
    Essay,
    FeedError,
    canonicalize_url,
    is_content_candidate,
    make_stable_id,
    normalize_text,
)
from paul_graham_essay_feeds.presentation import NULL_REPORTER
from paul_graham_essay_feeds.validate import validate_essays_structural


class _Parser(HTMLParser):
    """Collect anchors; flag those right after the site's essay-row gif."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[tuple[str, str, bool]] = []
        self._href: str | None = None
        self._parts: list[str] = []
        self._marked = False
        self._pending = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        amap = {k.lower(): v for k, v in attrs}
        if tag == "img":
            src = amap.get("src") or ""
            if urlsplit(src).path.endswith("/the-reddits-2.gif"):
                self._pending = True
            return
        if tag != "a":
            return
        href = amap.get("href")
        if href is None:
            return
        self._href = href.strip()
        self._parts = []
        self._marked = self._pending
        self._pending = False

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._href is None:
            return
        title = normalize_text("".join(self._parts))
        self.anchors.append((self._href, title, self._marked))
        self._href = None
        self._parts = []
        self._marked = False


def _to_essays(
    rows: list[tuple[str, str, bool]],
    *,
    base_url: str,
    dedupe_last: bool,
) -> list[Essay]:
    built: list[tuple[str, str, str, bool]] = []
    progress = NULL_REPORTER
    track = rows if len(rows) < 50 else progress.track(rows, desc="Parse anchors", unit="a")
    for href, title, _marked in track:
        if not title:
            continue
        try:
            url = canonicalize_url(base_url, href)
        except FeedError:
            continue
        if not is_content_candidate(url, title):
            continue
        sid, perm = make_stable_id(url)
        built.append((title, url, sid, perm))

    if dedupe_last:
        last = {sid: i for i, (*_, sid, _) in enumerate(built)}
        built = [row for i, row in enumerate(built) if last[row[2]] == i]

    return [
        Essay(position=i, title=t, url=u, stable_id=s, is_permalink=p)
        for i, (t, u, s, p) in enumerate(built, start=1)
    ]


def extract_essays(
    html: str,
    *,
    base_url: str = SOURCE_URL,
    min_items: int = MIN_ITEMS,
) -> list[Essay]:
    """Return newest→oldest essays; raise if too few or ACL chapters missing."""
    parser = _Parser()
    parser.feed(html)
    parser.close()
    logger.debug("Parsed {} anchors", len(parser.anchors))

    marked = [(h, t, m) for h, t, m in parser.anchors if m]
    essays = _to_essays(marked, base_url=base_url, dedupe_last=False)
    mode = "essay-row-marker"
    if len(essays) < min_items:
        logger.warning(
            "Marked rows only yielded {}; falling back to filtered anchors",
            len(essays),
        )
        essays = _to_essays(parser.anchors, base_url=base_url, dedupe_last=True)
        mode = "filtered-anchor-fallback"

    if len(essays) < min_items:
        raise FeedError(f"Only {len(essays)} essays (need ≥ {min_items})")

    ids = [e.stable_id for e in essays]
    dups = [i for i, c in Counter(ids).items() if c > 1]
    if dups:
        raise FeedError(f"Duplicate ids: {dups[:5]}")

    paths = {
        urlsplit(e.url).path
        for e in essays
        if (urlsplit(e.url).hostname or "") == "sep.turbifycdn.com"
    }
    missing = sorted(PROTECTED_PATHS - paths)
    if missing:
        raise FeedError(f"Missing protected ACL chapters: {', '.join(missing)}")

    validate_essays_structural(essays)
    logger.info("Extracted {} essays ({})", len(essays), mode)
    return essays
