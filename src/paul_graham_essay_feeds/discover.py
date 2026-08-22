"""Index HTML discovery: extraction strategies and typed diagnostics.

Row/cell-scoped marker binding, fail-closed fallback (F-017), first-wins
duplicates with anomaly reporting (F-025 / F-026), and an ``ExtractionReport``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field
from selectolax.parser import HTMLParser, Node

from paul_graham_essay_feeds.models import (
    MIN_ITEMS,
    PROTECTED_PATHS,
    SOURCE_URL,
    DiscoveryItem,
    FeedError,
    canonicalize_url,
    discovery_item_to_essay,
    is_content_candidate,
    make_stable_id,
    normalize_text,
)

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
    """Ordered discovery result: items and/or intermediate candidates."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    items: list[DiscoveryItem] = Field(
        default_factory=list,
        description="Final newest→oldest discovery items when discovery succeeded.",
    )
    candidates: list[DiscoveryCandidate] = Field(
        default_factory=list,
        description="All collected candidates with acceptance/rejection metadata.",
    )
    report: ExtractionReport = Field(
        description="Strategy and anomaly diagnostics for this pass.",
    )


@dataclass(slots=True)
class _MarkerWalkState:
    """Mutable marker→anchor binding state for a document-order DOM walk."""

    pending: bool = False
    marker_depth: int | None = None
    scope_depth: int = 0


def _clear_pending(state: _MarkerWalkState) -> None:
    state.pending = False
    state.marker_depth = None


def _walk_index_node(
    node: Node,
    state: _MarkerWalkState,
    anchors: list[tuple[str, str, bool]],
) -> None:
    """Document-order walk: scope-bounded marker binding (anti cross-row leak)."""
    tag = node.tag
    if tag is None:
        return
    tag_l = tag.lower()

    if tag_l in _SCOPE_TAGS:
        state.scope_depth += 1
        for child in node.iter(include_text=False):
            _walk_index_node(child, state, anchors)
        new_depth = state.scope_depth - 1
        if state.pending and state.marker_depth is not None and new_depth < state.marker_depth:
            # Left the row/cell that held the marker without binding an anchor.
            _clear_pending(state)
        state.scope_depth -= 1
        return

    if tag_l == "img":
        attrs = node.attributes or {}
        src = attrs.get("src") or ""
        if urlsplit(src).path.endswith(_MARKER_SUFFIX):
            state.pending = True
            state.marker_depth = state.scope_depth
        return

    if tag_l == "a":
        attrs = node.attributes or {}
        href = attrs.get("href")
        if href is not None:
            marked = False
            if (
                state.pending
                and state.marker_depth is not None
                and state.scope_depth >= state.marker_depth
            ):
                marked = True
                _clear_pending(state)
            elif state.pending:
                _clear_pending(state)
            title = normalize_text(node.text(separator=" "))
            anchors.append((href.strip(), title, marked))
        # Do not walk into <a> children (nested anchors are invalid HTML).
        return

    for child in node.iter(include_text=False):
        _walk_index_node(child, state, anchors)


def _collect_index_anchors(html: str) -> list[tuple[str, str, bool]]:
    """Parse index HTML with selectolax; return ``(href, title, marked)`` rows."""
    tree = HTMLParser(html)
    root = tree.root
    if root is None:
        return []
    anchors: list[tuple[str, str, bool]] = []
    _walk_index_node(root, _MarkerWalkState(), anchors)
    return anchors


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


def _to_discovery_items(accepted: list[DiscoveryCandidate]) -> list[DiscoveryItem]:
    items: list[DiscoveryItem] = []
    for i, cand in enumerate(accepted, start=1):
        if cand.url is None or cand.stable_id is None or cand.is_permalink is None:
            raise FeedError(f"Internal: accepted candidate incomplete: {cand.href!r}")
        items.append(
            DiscoveryItem(
                position=i,
                title=cand.title,
                url=cand.url,
                stable_id=cand.stable_id,
                is_permalink=cand.is_permalink,
            )
        )
    return items


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


def _require_protected(items: list[DiscoveryItem]) -> None:
    paths = {
        urlsplit(item.url).path
        for item in items
        if (urlsplit(item.url).hostname or "") == "sep.turbifycdn.com"
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
) -> tuple[list[DiscoveryItem], ExtractionReport]:
    """Discover newest→oldest index items from HTML with typed diagnostics.

    Marker strategy (default): bind ``the-reddits-2.gif`` to the next anchor
    within the same structural row/cell when possible.

    Fallback (``allow_fallback=True`` only): filtered content anchors when
    markers are too sparse. Default is fail-closed (F-017).

    Duplicates keep the first structurally valid occurrence and are recorded
    on the report (not silent last-wins).
    """
    rows = _collect_index_anchors(html)
    logger.debug("Discovery parsed {} anchors", len(rows))

    all_candidates = _collect_candidates(rows, base_url=base_url)
    marked_count = sum(1 for c in all_candidates if c.marked)
    marked_rows = [(h, t, m) for h, t, m in rows if m]
    marked_candidates = _collect_candidates(marked_rows, base_url=base_url)
    marked_accepted, marked_dups = _dedupe_first(marked_candidates)
    marked_items = _to_discovery_items(marked_accepted)

    fallback_used = False
    strategy = ExtractionStrategy.MARKER
    duplicates = list(marked_dups)
    # Rejections from the strategy that actually produced the set.
    working_candidates = marked_candidates

    if len(marked_items) < min_items:
        if not allow_fallback:
            raise FeedError(
                f"Only {len(marked_items)} essays from markers "
                f"(need ≥ {min_items}); fallback disabled"
            )
        logger.warning(
            "Marked rows only yielded {}; falling back to filtered anchors",
            len(marked_items),
        )
        fallback_used = True
        strategy = ExtractionStrategy.FALLBACK
        working_candidates = all_candidates
        accepted, duplicates = _dedupe_first(all_candidates)
        items = _to_discovery_items(accepted)
    else:
        items = marked_items

    rejections = _rejections_from(working_candidates)
    marked_accepted_count = len(marked_accepted)

    if len(items) < min_items:
        raise FeedError(f"Only {len(items)} essays (need ≥ {min_items})")

    _require_protected(items)
    # Lazy import: enrich absorbs validate helpers (T7); avoid import cycle at module load.
    from paul_graham_essay_feeds.enrich import validate_essays_structural

    validate_essays_structural([discovery_item_to_essay(item) for item in items])

    report = ExtractionReport(
        strategy=strategy,
        marked_count=marked_count,
        fallback_used=fallback_used,
        duplicates=duplicates,
        rejections=rejections,
        drift_score=_drift_score(
            marked_accepted=marked_accepted_count,
            final_count=len(items),
            fallback_used=fallback_used,
            duplicate_count=len(duplicates),
            rejection_count=len(rejections),
        ),
    )
    logger.info(
        "Discovered {} essays (strategy={}, fallback={}, drift={})",
        len(items),
        strategy,
        fallback_used,
        report.drift_score,
    )
    return items, report


def evaluate_discovery_anomaly(
    prior_ids: set[str] | frozenset[str],
    discovered_ids: set[str] | frozenset[str],
    *,
    report: ExtractionReport,
    max_removal_ratio: float = 0.15,
    max_addition_ratio: float = 0.50,
    min_overlap_ratio: float = 0.70,
) -> str | None:
    """Return a quarantine reason when discovery looks anomalous, else None.

    Floor-satisfying but partial extractions that would hard-delete a large
    share of the prior catalog are quarantined (H-03). Overlap is true set
    intersection over stable ids (RV-R-007) — same-size total swaps quarantine.
    """
    prior_count = len(prior_ids)
    if prior_count <= 0:
        return None
    removed_ids = prior_ids - discovered_ids
    added_ids = discovered_ids - prior_ids
    removed = len(removed_ids)
    added = len(added_ids)
    removal_ratio = removed / prior_count
    addition_ratio = added / prior_count
    overlap = len(prior_ids & discovered_ids) / prior_count
    if removal_ratio > max_removal_ratio and removed >= 5:
        return (
            f"discovery removal ratio {removal_ratio:.2%} "
            f"({removed} of {prior_count}) exceeds {max_removal_ratio:.0%}"
        )
    if addition_ratio > max_addition_ratio and added >= 20:
        return (
            f"discovery addition ratio {addition_ratio:.2%} "
            f"({added} of {prior_count}) exceeds {max_addition_ratio:.0%}"
        )
    if overlap < min_overlap_ratio and prior_count >= 20:
        return f"discovery overlap {overlap:.2%} with prior catalog below {min_overlap_ratio:.0%}"
    if report.fallback_used and removal_ratio > 0.05:
        return "fallback extraction with material removals"
    return None


def build_discovery_snapshot(
    html: str,
    *,
    base_url: str = SOURCE_URL,
    min_items: int = MIN_ITEMS,
    allow_fallback: bool = False,
) -> DiscoverySnapshot:
    """Run discovery and wrap items + all candidates into a snapshot."""
    candidates = _collect_candidates(_collect_index_anchors(html), base_url=base_url)
    items, report = discover_essays(
        html,
        base_url=base_url,
        min_items=min_items,
        allow_fallback=allow_fallback,
    )
    return DiscoverySnapshot(items=items, candidates=candidates, report=report)
