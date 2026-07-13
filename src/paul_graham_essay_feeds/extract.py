"""HTML extraction from the official Paul Graham essays index."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

from paul_graham_essay_feeds.domain import (
    PROTECTED_EXTERNAL_PATHS,
    SOURCE_URL,
    EssayItem,
    ExtractionResult,
    FeedError,
    canonicalize_url,
    is_content_candidate,
    make_stable_id,
    normalize_text,
    utc_now,
)

__all__ = ["Anchor", "EssayAnchorParser", "extract_items", "validate_extracted_items"]


@dataclass(frozen=True, slots=True)
class Anchor:
    """Raw visible anchor collected from the source HTML.

    Attributes
    ----------
    href :
        Raw ``href`` attribute text.
    title :
        Normalized visible anchor text.
    marked_as_essay :
        True when the site essay-row marker image immediately preceded this link.
    """

    href: str
    title: str
    marked_as_essay: bool


class EssayAnchorParser(HTMLParser):
    """Collect visible anchors and recognize the site's essay-row marker image."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[Anchor] = []
        self._href: str | None = None
        self._parts: list[str] = []
        self._current_marked = False
        self._pending_essay_marker = False

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        tag = tag.lower()
        attr_map = {key.lower(): value for key, value in attrs}

        if tag == "img":
            src = attr_map.get("src") or ""
            if urlsplit(src).path.endswith("/the-reddits-2.gif"):
                self._pending_essay_marker = True
            return

        if tag != "a":
            return

        href = attr_map.get("href")
        if href is None:
            return

        self._href = href.strip()
        self._parts = []
        self._current_marked = self._pending_essay_marker
        self._pending_essay_marker = False

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._href is None:
            return

        title = normalize_text("".join(self._parts))
        self.anchors.append(
            Anchor(
                href=self._href,
                title=title,
                marked_as_essay=self._current_marked,
            )
        )
        self._href = None
        self._parts = []
        self._current_marked = False


def _anchors_to_items(
    anchors: Iterable[Anchor],
    *,
    base_url: str,
    observed_at: datetime,
    deduplicate_by_last_occurrence: bool,
) -> tuple[list[EssayItem], int]:
    rows: list[tuple[str, str, str, bool]] = []
    for anchor in anchors:
        if not anchor.title:
            continue
        try:
            url = canonicalize_url(base_url, anchor.href)
        except FeedError:
            continue
        if not is_content_candidate(url, anchor.title):
            continue
        stable_id, is_permalink = make_stable_id(url)
        rows.append((anchor.title, url, stable_id, is_permalink))

    duplicate_count = 0
    if deduplicate_by_last_occurrence:
        last_index = {stable_id: index for index, (*_, stable_id, _) in enumerate(rows)}
        duplicate_count = len(rows) - len(last_index)
        rows = [row for index, row in enumerate(rows) if last_index[row[2]] == index]

    items = [
        EssayItem(
            position=index,
            title=title,
            url=url,
            stable_id=stable_id,
            is_permalink=is_permalink,
            first_seen_at=observed_at,
            last_changed_at=observed_at,
        )
        for index, (title, url, stable_id, is_permalink) in enumerate(rows, start=1)
    ]
    return items, duplicate_count


def validate_extracted_items(
    items: Sequence[EssayItem],
    *,
    min_items: int,
    require_protected_external: bool,
) -> None:
    """Raise :class:`FeedError` when extracted items violate safety invariants."""
    if len(items) < min_items:
        raise FeedError(f"Extracted {len(items)} items, below the safety floor of {min_items}.")

    identities = [item.identity for item in items]
    duplicates = sorted(identity for identity, count in Counter(identities).items() if count > 1)
    if duplicates:
        raise FeedError(f"Duplicate stable item identities: {duplicates}")

    links = [item.url for item in items]
    duplicate_links = sorted(link for link, count in Counter(links).items() if count > 1)
    if duplicate_links:
        raise FeedError(f"Duplicate item links: {duplicate_links}")

    for item in items:
        if not item.title or item.title != normalize_text(item.title):
            raise FeedError(f"Unnormalized or empty title at position {item.position}.")
        parts = urlsplit(item.url)
        if parts.scheme != "https":
            raise FeedError(f"Non-canonical item URL: {item.url}")
        if "paulgraham.com/https://" in item.url:
            raise FeedError(f"Malformed doubly-prefixed item URL: {item.url}")
        if item.is_permalink and item.stable_id != item.url:
            raise FeedError(f"Permalink stable_id does not equal item link: {item.url}")

    external_paths = {
        urlsplit(item.url).path
        for item in items
        if urlsplit(item.url).hostname == "sep.turbifycdn.com"
    }
    missing_external = sorted(PROTECTED_EXTERNAL_PATHS - external_paths)
    if require_protected_external and missing_external:
        raise FeedError(
            "The protected ANSI Common Lisp chapter links are missing: "
            + ", ".join(missing_external)
        )


def extract_items(
    source_html: str,
    *,
    base_url: str = SOURCE_URL,
    min_items: int,
    require_protected_external: bool = True,
    observed_at: datetime | None = None,
) -> ExtractionResult:
    """Extract and normalize essay items from source HTML.

    Uses the site's essay-row marker as the primary signal, with a controlled
    filtered-anchor fallback when the marked count falls below ``min_items``.
    """
    when = observed_at or utc_now()
    parser = EssayAnchorParser()
    parser.feed(source_html)
    parser.close()

    marked = [anchor for anchor in parser.anchors if anchor.marked_as_essay]
    marked_items, marked_duplicates = _anchors_to_items(
        marked,
        base_url=base_url,
        observed_at=when,
        deduplicate_by_last_occurrence=False,
    )

    if len(marked_items) >= min_items:
        items = marked_items
        mode = "essay-row-marker"
        duplicate_count = marked_duplicates
    else:
        items, duplicate_count = _anchors_to_items(
            parser.anchors,
            base_url=base_url,
            observed_at=when,
            deduplicate_by_last_occurrence=True,
        )
        mode = "filtered-anchor-fallback"

    validate_extracted_items(
        items,
        min_items=min_items,
        require_protected_external=require_protected_external,
    )
    return ExtractionResult(
        items=tuple(items),
        mode=mode,
        anchor_count=len(parser.anchors),
        marked_anchor_count=len(marked),
        duplicate_count=duplicate_count,
    )


def extraction_meta_dict(result: ExtractionResult) -> dict[str, Any]:
    """Serialize extraction metadata for reports."""
    return {
        "mode": result.mode,
        "anchor_count": result.anchor_count,
        "marked_anchor_count": result.marked_anchor_count,
        "duplicate_count": result.duplicate_count,
    }
