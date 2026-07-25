"""Index HTML discovery: extraction strategies and typed diagnostics.

Replaces the sticky-marker approach of ``extract.py`` with row/cell-scoped
marker binding, fail-closed fallback (F-017), first-wins duplicates with
anomaly reporting (F-025 / F-026), and an ``ExtractionReport``.
"""

from __future__ import annotations

from enum import StrEnum
from html.parser import HTMLParser
from urllib.parse import urlsplit

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

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
from paul_graham_essay_feeds.validate import validate_essays_structural

# Marker gif used on paulgraham.com essay-index rows.
_MARKER_SUFFIX = "/the-reddits-2.gif"
# Structural containers that bound marker→anchor association (anti-leak).
_SCOPE_TAGS = frozenset({"table", "tbody", "thead", "tfoot", "tr", "td", "th", "li"})


class ExtractionStrategy(StrEnum):
    """Which discovery strategy produced the accepted essay list."""

    MARKER = "essay-row-marker"
    FALLBACK = "filtered-anchor-fallback"


class DiscoveryCandidate(BaseModel):
    """One raw or normalized index-row candidate before final inclusion."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    href: str = Field(description="Raw href attribute from the index anchor.")
    title: str = Field(description="Normalized anchor text (may be empty when rejected).")
    marked: bool = Field(
        description="True when associated with a the-reddits-2.gif essay-row marker.",
    )
    source_index: int = Field(
        ge=0,
        description="0-based document order among collected anchors.",
    )
    url: str | None = Field(
        default=None,
        description="Absolute allowlisted https URL when canonicalize succeeded.",
    )
    stable_id: str | None = Field(
        default=None,
        description="Feed guid/id when the candidate was structurally accepted.",
    )
    is_permalink: bool | None = Field(
        default=None,
        description="True when stable_id is the essay URL (paulgraham.com).",
    )
    accepted: bool = Field(
        default=False,
        description="True when the candidate is eligible for inclusion (pre-dedupe).",
    )
    rejection_reason: str | None = Field(
        default=None,
        description="Why the candidate was rejected, if not accepted.",
    )


class ExtractionReport(BaseModel):
    """Typed diagnostics for one discovery pass (F-025 / F-026)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy: ExtractionStrategy = Field(
        description="Strategy that produced the final ordered essay list.",
    )
    marked_count: int = Field(
        ge=0,
        description="Number of anchors associated with essay-row markers.",
    )
    fallback_used: bool = Field(
        description="True when filtered-anchor fallback ran (only if allow_fallback).",
    )
    duplicates: list[str] = Field(
        default_factory=list,
        description="stable_id values seen more than once (later occurrences dropped).",
    )
    rejections: list[str] = Field(
        default_factory=list,
        description="Human-readable rejection records (href + reason).",
    )
    drift_score: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Extraction uncertainty in [0, 1]: 0 when pure marker path is solid; "
            "rises with fallback reliance, duplicates, and rejections."
        ),
    )


class DiscoverySnapshot(BaseModel):
    """Ordered discovery result: essays and/or intermediate candidates."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    essays: list[Essay] = Field(
        default_factory=list,
        description="Final newest→oldest essays when discovery succeeded.",
    )
    candidates: list[DiscoveryCandidate] = Field(
        default_factory=list,
        description="All collected candidates with acceptance/rejection metadata.",
    )
    report: ExtractionReport = Field(
        description="Strategy and anomaly diagnostics for this pass.",
    )


class _IndexParser(HTMLParser):
    """Collect anchors; bind marker gifs to anchors within the same row/cell.

    Improves on sticky ``_pending`` by clearing pending when the structural
    scope that contained the marker (tr/td/li/…) is closed, so a marker cannot
    leak across table rows into unrelated anchors.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[tuple[str, str, bool]] = []
        self._href: str | None = None
        self._parts: list[str] = []
        self._marked = False
        self._pending = False
        self._scope_stack: list[str] = []
        # Depth of ``_scope_stack`` when the pending marker was seen (0 = flat).
        self._marker_depth: int | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _SCOPE_TAGS:
            self._scope_stack.append(tag)
            return
        amap = {k.lower(): v for k, v in attrs}
        if tag == "img":
            src = amap.get("src") or ""
            if urlsplit(src).path.endswith(_MARKER_SUFFIX):
                self._pending = True
                self._marker_depth = len(self._scope_stack)
            return
        if tag != "a":
            return
        href = amap.get("href")
        if href is None:
            return
        self._href = href.strip()
        self._parts = []
        self._marked = False
        if self._pending and self._marker_in_scope():
            self._marked = True
            self._clear_pending()
        elif self._pending and not self._marker_in_scope():
            # Scope already left (should be rare if endtag handling ran).
            self._clear_pending()

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _SCOPE_TAGS and self._scope_stack and self._scope_stack[-1] == tag:
            new_depth = len(self._scope_stack) - 1
            if self._pending and self._marker_depth is not None and new_depth < self._marker_depth:
                # Left the row/cell that held the marker without binding an anchor.
                self._clear_pending()
            self._scope_stack.pop()
        if tag != "a" or self._href is None:
            return
        title = normalize_text("".join(self._parts))
        self.anchors.append((self._href, title, self._marked))
        self._href = None
        self._parts = []
        self._marked = False

    def _marker_in_scope(self) -> bool:
        if self._marker_depth is None:
            return False
        # Marker applies while we remain at or inside the marker's scope depth.
        return len(self._scope_stack) >= self._marker_depth

    def _clear_pending(self) -> None:
        self._pending = False
        self._marker_depth = None


def _rejection_record(href: str, reason: str) -> str:
    return f"{href}: {reason}"


def _classify_anchor(
    href: str,
    title: str,
    marked: bool,
    source_index: int,
    *,
    base_url: str,
) -> DiscoveryCandidate:
    """Normalize one anchor into an accepted or rejected candidate."""
    if not title:
        return DiscoveryCandidate(
            href=href,
            title=title,
            marked=marked,
            source_index=source_index,
            accepted=False,
            rejection_reason="empty title",
        )
    try:
        url = canonicalize_url(base_url, href)
    except FeedError as exc:
        return DiscoveryCandidate(
            href=href,
            title=title,
            marked=marked,
            source_index=source_index,
            accepted=False,
            rejection_reason=str(exc),
        )
    if not is_content_candidate(url, title):
        return DiscoveryCandidate(
            href=href,
            title=title,
            marked=marked,
            source_index=source_index,
            url=url,
            accepted=False,
            rejection_reason="not a content candidate",
        )
    stable_id, is_permalink = make_stable_id(url)
    return DiscoveryCandidate(
        href=href,
        title=title,
        marked=marked,
        source_index=source_index,
        url=url,
        stable_id=stable_id,
        is_permalink=is_permalink,
        accepted=True,
        rejection_reason=None,
    )


def _dedupe_first(
    candidates: list[DiscoveryCandidate],
) -> tuple[list[DiscoveryCandidate], list[str]]:
    """Prefer the first structurally valid occurrence; record later dups."""
    seen: set[str] = set()
    kept: list[DiscoveryCandidate] = []
    duplicates: list[str] = []
    for cand in candidates:
        if not cand.accepted or cand.stable_id is None:
            continue
        if cand.stable_id in seen:
            duplicates.append(cand.stable_id)
            continue
        seen.add(cand.stable_id)
        kept.append(cand)
    return kept, duplicates


def _to_essays(accepted: list[DiscoveryCandidate]) -> list[Essay]:
    essays: list[Essay] = []
    for i, cand in enumerate(accepted, start=1):
        if cand.url is None or cand.stable_id is None or cand.is_permalink is None:
            raise FeedError(f"Internal: accepted candidate incomplete: {cand.href!r}")
        essays.append(
            Essay(
                position=i,
                title=cand.title,
                url=cand.url,
                stable_id=cand.stable_id,
                is_permalink=cand.is_permalink,
            )
        )
    return essays


def _drift_score(
    *,
    marked_accepted: int,
    final_count: int,
    fallback_used: bool,
    duplicate_count: int,
    rejection_count: int,
) -> float:
    """Bounded uncertainty score for diagnostics (not a hard gate)."""
    if final_count <= 0:
        return 1.0
    score = 0.0
    if fallback_used:
        # How much of the final set could not be explained by markers.
        score = max(0.0, 1.0 - (marked_accepted / final_count))
    score += 0.05 * min(duplicate_count, 4)
    score += 0.01 * min(rejection_count, 10)
    return round(min(1.0, score), 4)


def _collect_candidates(
    rows: list[tuple[str, str, bool]],
    *,
    base_url: str,
) -> list[DiscoveryCandidate]:
    return [
        _classify_anchor(href, title, marked, index, base_url=base_url)
        for index, (href, title, marked) in enumerate(rows)
    ]


def _rejections_from(candidates: list[DiscoveryCandidate]) -> list[str]:
    return [
        _rejection_record(c.href, c.rejection_reason or "rejected")
        for c in candidates
        if not c.accepted
    ]


def _require_protected(essays: list[Essay]) -> None:
    paths = {
        urlsplit(e.url).path
        for e in essays
        if (urlsplit(e.url).hostname or "") == "sep.turbifycdn.com"
    }
    missing = sorted(PROTECTED_PATHS - paths)
    if missing:
        raise FeedError(f"Missing protected ACL chapters: {', '.join(missing)}")


def discover_essays(
    html: str,
    *,
    base_url: str = SOURCE_URL,
    min_items: int = MIN_ITEMS,
    allow_fallback: bool = False,
) -> tuple[list[Essay], ExtractionReport]:
    """Discover newest→oldest essays from index HTML with typed diagnostics.

    Marker strategy (default): bind ``the-reddits-2.gif`` to the next anchor
    within the same structural row/cell when possible.

    Fallback (``allow_fallback=True`` only): filtered content anchors when
    markers are too sparse. Default is fail-closed (F-017).

    Duplicates keep the first structurally valid occurrence and are recorded
    on the report (not silent last-wins).
    """
    parser = _IndexParser()
    parser.feed(html)
    parser.close()
    logger.debug("Discovery parsed {} anchors", len(parser.anchors))

    all_candidates = _collect_candidates(parser.anchors, base_url=base_url)
    marked_count = sum(1 for c in all_candidates if c.marked)
    marked_rows = [(h, t, m) for h, t, m in parser.anchors if m]
    marked_candidates = _collect_candidates(marked_rows, base_url=base_url)
    marked_accepted, marked_dups = _dedupe_first(marked_candidates)
    marked_essays = _to_essays(marked_accepted)

    fallback_used = False
    strategy = ExtractionStrategy.MARKER
    duplicates = list(marked_dups)
    # Rejections from the strategy that actually produced the set.
    working_candidates = marked_candidates

    if len(marked_essays) < min_items:
        if not allow_fallback:
            raise FeedError(
                f"Only {len(marked_essays)} essays from markers "
                f"(need ≥ {min_items}); fallback disabled"
            )
        logger.warning(
            "Marked rows only yielded {}; falling back to filtered anchors",
            len(marked_essays),
        )
        fallback_used = True
        strategy = ExtractionStrategy.FALLBACK
        working_candidates = all_candidates
        accepted, duplicates = _dedupe_first(all_candidates)
        essays = _to_essays(accepted)
    else:
        essays = marked_essays

    rejections = _rejections_from(working_candidates)
    marked_accepted_count = len(marked_accepted)

    if len(essays) < min_items:
        raise FeedError(f"Only {len(essays)} essays (need ≥ {min_items})")

    _require_protected(essays)
    validate_essays_structural(essays)

    report = ExtractionReport(
        strategy=strategy,
        marked_count=marked_count,
        fallback_used=fallback_used,
        duplicates=duplicates,
        rejections=rejections,
        drift_score=_drift_score(
            marked_accepted=marked_accepted_count,
            final_count=len(essays),
            fallback_used=fallback_used,
            duplicate_count=len(duplicates),
            rejection_count=len(rejections),
        ),
    )
    logger.info(
        "Discovered {} essays (strategy={}, fallback={}, drift={})",
        len(essays),
        strategy,
        fallback_used,
        report.drift_score,
    )
    return essays, report


def build_discovery_snapshot(
    html: str,
    *,
    base_url: str = SOURCE_URL,
    min_items: int = MIN_ITEMS,
    allow_fallback: bool = False,
) -> DiscoverySnapshot:
    """Run discovery and wrap essays + all candidates into a snapshot."""
    parser = _IndexParser()
    parser.feed(html)
    parser.close()
    candidates = _collect_candidates(parser.anchors, base_url=base_url)
    essays, report = discover_essays(
        html,
        base_url=base_url,
        min_items=min_items,
        allow_fallback=allow_fallback,
    )
    return DiscoverySnapshot(essays=essays, candidates=candidates, report=report)
