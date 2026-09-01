"""Catalog-native update pipeline (v7.1).

load/bootstrap catalog → discover → reconcile → refresh plan
→ live-probe URLs not due for enrich → selective enrich (GET implies reachability)
→ snapshot → verify in memory → atomic root ``catalog.json`` + ``feeds/*``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from loguru import logger

from paul_graham_essay_feeds import __version__
from paul_graham_essay_feeds.catalog import (
    ChangeSet,
    RefreshDecision,
    RefreshPlan,
    bootstrap_catalog_from_feeds,
    catalog_with_page_fetch_cursor,
    default_catalog_path,
    failure_backoff_delta,
    load_catalog,
    plan_refresh,
    reconcile_discovery,
    save_catalog,
    stamp_state_revision,
)
from paul_graham_essay_feeds.discover import discover_essays
from paul_graham_essay_feeds.enrich import (
    LinkProbeReport,
    PageEnrichEvidence,
    enrich_essays,
    score_summary_quality,
    validate_essays_live,
)
from paul_graham_essay_feeds.feeds import (
    all_feed_paths,
    catalog_to_feed_snapshot,
    feeds_exist,
    render_snapshot_feeds,
)
from paul_graham_essay_feeds.http import HostCooldown, decode_html_document, fetch_index
from paul_graham_essay_feeds.models import (
    GENERATOR,
    NULL_REPORTER,
    Catalog,
    CatalogEntry,
    Essay,
    FeedError,
    ProgressReporter,
    ResourceState,
    blurb,
    content_sha256,
    discovery_item_to_essay,
    require_aware_utc,
    utc_now,
)
from paul_graham_essay_feeds.settings import Settings, budget_label
from paul_graham_essay_feeds.verify import (
    assert_verified,
    raise_on_failure,
    summary_passes_semantic_gate,
    verify_feed_dir,
)

_HTTP_CACHE_REL: Path = Path(".cache") / "http-cache.json"
_LINK_VALIDATION_CURSOR_KEY = "link_validation_cursor"
_MATERIAL_CHANGED_PATHS: tuple[str, ...] = (
    "catalog.json",
    "feeds/rss.xml",
    "feeds/atom.xml",
    "feeds/feed.json",
    "feeds/rss.simple.xml",
    "feeds/atom.simple.xml",
    "feeds/feed.simple.json",
)


class PipelineAction(StrEnum):
    """Durable outcome of one pipeline pass (machine side-channel values).

    Wire strings for ``--result-file`` / ``$GITHUB_OUTPUT``:

    * ``unchanged`` — no tracked durable writes (catalog/feeds untouched)
    * ``state_changed`` — catalog state written; all six feed bytes identical
    * ``updated`` — material feed projections (and catalog) written
    """

    NO_CHANGE = "unchanged"
    STATE_CHANGED = "state_changed"
    MATERIAL_CHANGED = "updated"


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """Outcome of one catalog-pipeline update pass."""

    catalog: Catalog
    changeset: ChangeSet
    refresh_plan: RefreshPlan
    index_hash: str
    essay_count: int
    skipped: bool
    action: str  # PipelineAction value: unchanged | state_changed | updated
    changed_paths: tuple[str, ...] = ()
    links_checked: int = 0
    links_skipped: int = 0
    links_healthy: int = 0
    links_failed: int = 0
    links_failed_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _LockedWrite:
    """Result of one lock-protected catalog-only-or-publish decision."""

    catalog: Catalog
    action: str  # unchanged | state_changed | updated


def _read_source_file(path: Path, *, max_bytes: int) -> tuple[str, str]:
    try:
        size = path.stat().st_size
    except OSError:
        size = -1
    if size > max_bytes:
        raise FeedError(f"Source file over {max_bytes} bytes: {path}")
    raw = path.read_bytes()
    if len(raw) > max_bytes:
        raise FeedError(f"Source file over {max_bytes} bytes: {path}")
    document = decode_html_document(raw)
    return document.text, document.encoding


def _material_config_fingerprint(settings: Settings) -> str:
    """Fingerprint of settings that affect feed material content."""
    parts = (
        f"min={settings.min_items}",
        f"enrich={settings.enrich}",
        "summary_src=meta|body",
        f"public={settings.public_base_url or ''}",
        f"v={__version__}",
    )
    return content_sha256("|".join(parts))[:16]


def _load_or_bootstrap_catalog(root: Path, *, now: datetime, fingerprint: str) -> Catalog:
    path = default_catalog_path(root)
    existing = load_catalog(path)
    if existing is not None:
        return existing
    logger.info("No durable catalog at {}; bootstrapping from feeds/ if present", path)
    return bootstrap_catalog_from_feeds(
        root,
        now=now,
        material_config_fingerprint=fingerprint,
    )


def _index_identity_fingerprint(essays: list[Essay]) -> str | None:
    """Stable multi-line index identity fingerprint from discovery essays."""
    if not essays:
        return None
    return "\n".join(essay.index_fingerprint() for essay in essays)


def _complete_index_state(
    *,
    prior: ResourceState,
    observed: datetime,
    etag: str | None,
    last_modified: str | None,
    raw_sha256: str | None,
    decoded_sha256: str | None,
    status_code: int | None,
    raw_bytes_received: int | None = None,
    decoded_bytes_received: int | None = None,
    selected_encoding: str | None = None,
) -> ResourceState:
    """Build a full schema-v2 index ResourceState for an accepted observation.

    Does not invent ``raw_sha256`` when transport omitted it (RV-R-004).
    Accepted 200 persists hashes, byte counts, and selected encoding.
    304 preserves prior hashes/counts/encoding while advancing clocks
    (PGF-2026-010).
    """
    next_etag = etag if etag is not None else prior.etag
    next_last_modified = last_modified if last_modified is not None else prior.last_modified
    next_status = status_code if status_code is not None else prior.status_code
    if status_code == 304:
        return prior.model_copy(
            update={
                "etag": next_etag,
                "last_modified": next_last_modified,
                "last_checked_at": observed,
                "last_attempted_at": observed,
                "last_response_at": observed,
                "last_success_at": observed,
                "failure_count": 0,
                "last_error_kind": None,
                "last_error_message": None,
                "next_retry_at": None,
                "status_code": 304,
            }
        )
    return ResourceState(
        etag=next_etag,
        last_modified=next_last_modified,
        raw_sha256=raw_sha256 if raw_sha256 is not None else prior.raw_sha256,
        decoded_sha256=decoded_sha256 if decoded_sha256 is not None else prior.decoded_sha256,
        raw_bytes_received=(
            raw_bytes_received if raw_bytes_received is not None else prior.raw_bytes_received
        ),
        decoded_bytes_received=(
            decoded_bytes_received
            if decoded_bytes_received is not None
            else prior.decoded_bytes_received
        ),
        last_checked_at=observed,
        last_attempted_at=observed,
        last_response_at=observed,
        last_success_at=observed,
        failure_count=0,
        last_error_kind=None,
        last_error_message=None,
        next_retry_at=None,
        status_code=next_status,
        selected_encoding=(
            selected_encoding if selected_encoding is not None else prior.selected_encoding
        ),
    )


def _essays_for_ids(essays: list[Essay], ids: set[str]) -> list[Essay]:
    return [e for e in essays if e.stable_id in ids]


def _scored_summary(summary: str | None) -> tuple[float | None, tuple[str, ...]]:
    if summary is None or not summary.strip():
        return None, ()
    score, flags = score_summary_quality(summary)
    return score, flags


def _apply_enrichment(
    catalog: Catalog,
    enriched: list[Essay],
    *,
    now: datetime,
    page_evidence: Mapping[str, PageEnrichEvidence] | None = None,
) -> Catalog:
    """Merge enrichment fields into catalog entries (preserve prior-good).

    Successful validation (HTTP 304 or accepted 200) advances success clocks.
    Soft-fail / transport / parse failures advance attempt clocks and backoff
    only — they never mint a success TTL.
    """
    by_id = {e.stable_id: e for e in enriched}
    evidence_by_id = page_evidence or {}
    next_entries: dict[str, CatalogEntry] = dict(catalog.entries)
    for stable_id, essay in by_id.items():
        entry = next_entries.get(stable_id)
        if entry is None:
            continue
        ev = evidence_by_id.get(stable_id)

        # Failure path: no evidence, or explicit ok=False.
        if ev is None or not ev.ok:
            fail_count = int(entry.page.failure_count) + 1
            fail_update: dict[str, object] = {
                "last_checked_at": now,
                "last_attempted_at": now,
                "last_response_at": now,
                "failure_count": fail_count,
                "last_error_kind": (ev.error_kind if ev is not None else "missing_evidence"),
                "last_error_message": (
                    ev.error_message if ev is not None else "no enrich evidence"
                ),
                "next_retry_at": now + failure_backoff_delta(failure_count=fail_count),
                "status_code": ev.status_code if ev is not None else entry.page.status_code,
            }
            if ev is not None:
                if ev.etag is not None:
                    fail_update["etag"] = ev.etag
                if ev.last_modified is not None:
                    fail_update["last_modified"] = ev.last_modified
                if ev.raw_sha256 is not None:
                    fail_update["raw_sha256"] = ev.raw_sha256
                if ev.decoded_sha256 is not None:
                    fail_update["decoded_sha256"] = ev.decoded_sha256
                if ev.raw_bytes_received is not None:
                    fail_update["raw_bytes_received"] = ev.raw_bytes_received
                if ev.decoded_bytes_received is not None:
                    fail_update["decoded_bytes_received"] = ev.decoded_bytes_received
                if ev.selected_encoding is not None:
                    fail_update["selected_encoding"] = ev.selected_encoding
            page = entry.page.model_copy(update=fail_update)
            next_entries[stable_id] = entry.model_copy(update={"page": page})
            continue

        if ev.not_modified:
            # Parse-failed validators must not mint a 304 success (audit P0-1).
            if entry.page.last_error_kind == "parse":
                fail_count = int(entry.page.failure_count) + 1
                page = entry.page.model_copy(
                    update={
                        "last_checked_at": now,
                        "last_attempted_at": now,
                        "last_response_at": now,
                        "failure_count": fail_count,
                        "last_error_kind": "parse",
                        "last_error_message": (
                            entry.page.last_error_message or "parse-failed representation"
                        ),
                        "next_retry_at": now + failure_backoff_delta(failure_count=fail_count),
                        "status_code": 304,
                    }
                )
                next_entries[stable_id] = entry.model_copy(update={"page": page})
                continue
            # 304: retain prior-good / summary; successful validation clocks only.
            # Preserve hashes, byte counts, and selected_encoding (PGF-2026-010).
            page = entry.page.model_copy(
                update={
                    "etag": ev.etag or entry.page.etag,
                    "last_modified": ev.last_modified or entry.page.last_modified,
                    "last_checked_at": now,
                    "last_attempted_at": now,
                    "last_response_at": now,
                    "last_success_at": now,
                    "failure_count": 0,
                    "last_error_kind": None,
                    "last_error_message": None,
                    "next_retry_at": None,
                    "status_code": 304,
                }
            )
            next_entries[stable_id] = entry.model_copy(update={"page": page})
            continue

        new_summary = essay.summary if essay.summary and essay.summary.strip() else None
        if essay.quality_score is None:
            new_score, new_flags = _scored_summary(new_summary)
        else:
            new_score = essay.quality_score
            new_flags = essay.quality_flags
        new_ok = summary_passes_semantic_gate(
            new_summary,
            score=new_score,
            flags=new_flags,
        )
        prior_good = entry.prior_good_summary
        prior_ok = summary_passes_semantic_gate(prior_good)

        if new_ok:
            assert new_summary is not None
            effective = new_summary
            source = essay.summary_source
            quality = new_score
            flags = new_flags
            prior_good = new_summary
        elif prior_ok:
            effective = prior_good
            source = entry.summary_source
            quality = entry.summary_quality
            flags = entry.quality_flags
        else:
            effective = blurb(entry.title)
            source = "title"
            quality, flags = score_summary_quality(effective)
            prior_good = effective if summary_passes_semantic_gate(effective) else None

        next_decoded = (
            ev.decoded_sha256
            if ev.decoded_sha256 is not None
            else (essay.content_hash or entry.page.decoded_sha256)
        )
        material = (
            (effective or "") != (entry.summary or "")
            or (essay.published_hint or None) != (entry.published_hint or None)
            or essay.published_at != entry.published_at
            or (next_decoded is not None and next_decoded != entry.page.decoded_sha256)
        )
        # Successful 200 body path: persist hashes, byte counts, encoding.
        page_etag = ev.etag if ev.status_code == 200 else entry.page.etag
        page_last_modified = ev.last_modified if ev.status_code == 200 else entry.page.last_modified
        page_status = ev.status_code if ev.status_code is not None else 200
        page = ResourceState(
            etag=page_etag,
            last_modified=page_last_modified,
            raw_sha256=ev.raw_sha256 if ev.raw_sha256 is not None else entry.page.raw_sha256,
            decoded_sha256=next_decoded,
            raw_bytes_received=(
                ev.raw_bytes_received
                if ev.raw_bytes_received is not None
                else entry.page.raw_bytes_received
            ),
            decoded_bytes_received=(
                ev.decoded_bytes_received
                if ev.decoded_bytes_received is not None
                else entry.page.decoded_bytes_received
            ),
            last_checked_at=now,
            last_attempted_at=now,
            last_response_at=now,
            last_success_at=now,
            failure_count=0,
            last_error_kind=None,
            last_error_message=None,
            next_retry_at=None,
            status_code=page_status,
            selected_encoding=(
                ev.selected_encoding
                if ev.selected_encoding is not None
                else entry.page.selected_encoding
            ),
        )
        next_entries[stable_id] = entry.model_copy(
            update={
                "summary": effective,
                "summary_source": source,
                "summary_quality": quality,
                "quality_flags": flags,
                "prior_good_summary": prior_good,
                "published_hint": essay.published_hint or entry.published_hint,
                "published_at": essay.published_at or entry.published_at,
                "page": page,
                "observed_updated_at": now if material else entry.observed_updated_at,
            }
        )
    return catalog.model_copy(update={"entries": next_entries})


def material_catalog_digest(catalog: Catalog) -> str:
    """Hash feed-visible catalog material (exclude clocks, schema, wire hashes).

    Materiality is decoded content plus rendered fields: title, url, summary*,
    published_*, order, and page ``decoded_sha256``. ``raw_sha256`` is
    provenance-only and must not change this digest (PGF-2026-009).
    """
    material_entries: dict[str, dict[str, object]] = {}
    for stable_id, entry in catalog.entries.items():
        material_entries[stable_id] = {
            "stable_id": entry.stable_id,
            "url": entry.url,
            "title": entry.title,
            "position": entry.position,
            "observed_updated_at": (
                entry.observed_updated_at.isoformat()
                if entry.observed_updated_at is not None
                else None
            ),
            "published_at": (
                entry.published_at.isoformat() if entry.published_at is not None else None
            ),
            "published_hint": entry.published_hint,
            "summary": entry.summary,
            "summary_source": entry.summary_source,
            "page_decoded_sha256": entry.page.decoded_sha256,
        }
    payload = {
        "material_config_fingerprint": catalog.material_config_fingerprint,
        "entry_order": list(catalog.entry_order),
        "entries": material_entries,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


def _should_skip_publish(
    *,
    root: Path,
    force: bool,
    changeset: ChangeSet,
    plan: RefreshPlan,
) -> bool:
    """Skip enrich and page fetches when reconcile is inert and no page work is due.

    Does **not** skip the writer critical section (lock / recover / verify).
    Does **not** skip ``validate_links`` (PGF-2026-005): dedicated probes are an
    independent planned phase. Missing root ``catalog.json`` or any of the six
    ``feeds/`` files always proceeds to enrich/publish (never a no-op).
    """
    if force:
        return False
    if not feeds_exist(root):
        return False
    if not default_catalog_path(root).is_file():
        return False
    if changeset.added or changeset.updated or changeset.removed or changeset.held:
        return False
    return not any(d.fetch_page for d in plan.decisions)


def _feed_bytes_match(path: Path, expected: bytes) -> bool:
    try:
        return path.read_bytes() == expected
    except OSError:
        return False


def _material_unchanged_vs_disk(
    root: Path,
    *,
    catalog: Catalog,
    rss: bytes,
    atom: bytes,
    json_feed: bytes,
    simple_rss: bytes,
    simple_atom: bytes,
    simple_json_feed: bytes,
) -> bool:
    """True when post-enrich material matches on-disk catalog + all six feeds."""
    if not feeds_exist(root):
        return False
    existing = load_catalog(default_catalog_path(root))
    if existing is None:
        return False
    if material_catalog_digest(existing) != material_catalog_digest(catalog):
        return False
    paths = all_feed_paths(root)
    return (
        _feed_bytes_match(paths["rss"], rss)
        and _feed_bytes_match(paths["atom"], atom)
        and _feed_bytes_match(paths["json"], json_feed)
        and _feed_bytes_match(paths["rss_simple"], simple_rss)
        and _feed_bytes_match(paths["atom_simple"], simple_atom)
        and _feed_bytes_match(paths["json_simple"], simple_json_feed)
    )


def _overlay_observation_clocks(*, base: Catalog, clocks: Catalog) -> Catalog:
    """Keep recovered material/identity; apply this-run observation clocks.

    ``last_generation_id`` and material fields stay on ``base`` (the rematerialized
    generation). Index/page resource state, ``last_seen_at``, absence streaks,
    and version cursors come from ``clocks`` (this run).
    """
    merged_entries: dict[str, CatalogEntry] = {}
    for sid, entry in base.entries.items():
        src = clocks.entries.get(sid)
        if src is None:
            merged_entries[sid] = entry
            continue
        merged_entries[sid] = entry.model_copy(
            update={
                "last_seen_at": src.last_seen_at,
                "page": src.page,
                "consecutive_absences": src.consecutive_absences,
            }
        )
    return base.model_copy(
        update={
            "index": clocks.index,
            "versions": {**base.versions, **clocks.versions},
            "entries": merged_entries,
        }
    )


def _public_bundle_present(root: Path) -> bool:
    """True when root ``catalog.json`` and all six ``feeds/`` artifacts exist."""
    return feeds_exist(root) and default_catalog_path(root).is_file()


def _version_cursor(versions: Mapping[str, str], key: str) -> int:
    """Parse a non-negative integer cursor from ``catalog.versions``."""
    raw = versions.get(key, "0")
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def _rotate_probe_essays(
    essays: list[Essay],
    *,
    cursor: int,
    limit: int | None,
) -> tuple[list[Essay], int]:
    """Rotate eligible probe essays from ``cursor`` and cap; return slice + next cursor.

    ``next_cursor`` advances by the number **attempted** (len of the returned
    slice), not by successes. An empty attempt leaves the cursor unchanged.
    """
    n = len(essays)
    if n == 0:
        return [], 0
    start = cursor % n
    rotated = essays[start:] + essays[:start]
    selected = list(rotated) if limit is None else rotated[: max(0, limit)]
    attempted = len(selected)
    next_cursor = (start + attempted) % n if attempted else start
    return selected, next_cursor


def _verify_public_bundle(
    root: Path,
    *,
    min_items: int,
    public_base_url: str | None,
) -> None:
    """Deep-verify the on-disk enriched and simple triples (AUD-001 no-op path)."""
    raise_on_failure(
        verify_feed_dir(
            root,
            min_items=min_items,
            kind="enriched",
            public_base_url=public_base_url,
        )
    )
    raise_on_failure(
        verify_feed_dir(
            root,
            min_items=min_items,
            kind="simple",
            public_base_url=public_base_url,
        )
    )


def _result_for_locked_write(
    committed: _LockedWrite,
    *,
    changeset: ChangeSet,
    refresh_plan: RefreshPlan,
    index_hash: str,
    essay_count: int,
    links_checked: int = 0,
    links_skipped: int = 0,
    links_healthy: int = 0,
    links_failed: int = 0,
    links_failed_ids: tuple[str, ...] = (),
) -> PipelineResult:
    """Map a lock-section outcome onto ``PipelineResult`` side-channel fields."""
    if committed.action == PipelineAction.NO_CHANGE.value:
        changed_paths: tuple[str, ...] = ()
        skipped = True
    elif committed.action == PipelineAction.STATE_CHANGED.value:
        changed_paths = ("catalog.json",)
        skipped = True
    else:
        changed_paths = _MATERIAL_CHANGED_PATHS
        skipped = False
    return PipelineResult(
        catalog=committed.catalog,
        changeset=changeset,
        refresh_plan=refresh_plan,
        index_hash=index_hash,
        essay_count=essay_count,
        skipped=skipped,
        action=committed.action,
        changed_paths=changed_paths,
        links_checked=links_checked,
        links_skipped=links_skipped,
        links_healthy=links_healthy,
        links_failed=links_failed,
        links_failed_ids=links_failed_ids,
    )


def _stage_and_materialize(
    root: Path,
    *,
    catalog: Catalog,
    rss: bytes,
    atom: bytes,
    json_feed: bytes,
    simple_rss: bytes,
    simple_atom: bytes,
    simple_json_feed: bytes,
    reporter: ProgressReporter,
) -> str:
    """Stage then materialize public feeds+catalog. Return the stamped generation id.

    Caller must hold the writer lock. The returned id is the same token written
    into staged/public ``catalog.last_generation_id``.
    """
    from paul_graham_essay_feeds.publication import (
        materialize_generation,
        write_staging_generation,
    )

    gen_id = write_staging_generation(
        root,
        catalog=catalog,
        rss=rss,
        atom=atom,
        json_feed=json_feed,
        simple_rss=simple_rss,
        simple_atom=simple_atom,
        simple_json_feed=simple_json_feed,
    )
    materialize_generation(root, gen_id=gen_id, reporter=reporter)
    return gen_id


def _finalize_under_lock(
    root: Path,
    catalog: Catalog,
    *,
    rss: bytes,
    atom: bytes,
    json_feed: bytes,
    simple_rss: bytes,
    simple_atom: bytes,
    simple_json_feed: bytes,
    reporter: ProgressReporter,
    overlay_clocks: bool,
    verify_existing_on_noop: bool,
    force_publish: bool,
    min_items: int,
    public_base_url: str | None,
    base_material_digest: str | None = None,
    base_state_revision: str | None = None,
) -> _LockedWrite:
    """Single writer critical section: lock → recover → verify/recompute → write.

    Network stays outside. ``verify_existing_on_noop`` (skip-enrich path) deep-
    verifies the on-disk seven-file bundle before allowing ``unchanged``. Catalog
    material digest (not feed-byte equality) decides skip no-op vs publish so a
    304 index_hash representation cannot churn feeds. Catalog-only compares
    digest **and** feed bytes. ``force_publish`` always stages after recover
    unless the durable catalog material digest differs from
    ``base_material_digest`` (stale candidate; PGF-2026-002).
    ``base_state_revision`` must match the durable catalog's ``state_revision``
    or finalize aborts (PGF-2026-030). Recovery is an intervening durable write
    (it rematerializes a generation that already minted a revision) and does
    not skip compare-and-swap. Same-material overlay cannot regress clocks,
    cursors, or streaks from an older contender.
    """
    from paul_graham_essay_feeds.publication import (
        acquire_write_lock,
        recover_materialize,
        release_write_lock,
    )

    lock = acquire_write_lock(root)
    try:
        recover_materialize(root)
        reloaded = load_catalog(default_catalog_path(root))
        # Recover first so public state is repaired, then compare unconditionally.
        # A recovered generation minted its own state_revision; that is why the
        # candidate planned from the pre-recovery base must abort and rebase
        # (PGF-2026-030). A completed concurrent writer unlinks the pointer so
        # recover is a no-op and a revision mismatch still fail-closes.
        # A missing catalog is not "no competing writer" when this candidate
        # planned from a real revision (audit P1-8).
        if reloaded is None and base_state_revision is not None:
            raise FeedError(
                "Stale finalize: durable catalog is missing after recover; re-run to rebase."
            )
        if reloaded is not None and reloaded.state_revision != base_state_revision:
            raise FeedError(
                "Stale finalize: durable catalog state revision changed since this "
                f"candidate was planned (base {base_state_revision!r}, "
                f"current {reloaded.state_revision!r}). Re-run to rebase."
            )
        if reloaded is not None and base_material_digest is not None:
            current_digest = material_catalog_digest(reloaded)
            candidate_digest = material_catalog_digest(catalog)
            if current_digest != base_material_digest and current_digest != candidate_digest:
                raise FeedError(
                    "Stale finalize: durable catalog material changed since this "
                    f"candidate was planned (base {base_material_digest[:16]}, "
                    f"current {current_digest[:16]}). Re-run to rebase."
                )
        if verify_existing_on_noop and _public_bundle_present(root):
            _verify_public_bundle(
                root,
                min_items=min_items,
                public_base_url=public_base_url,
            )
        if not force_publish:
            if verify_existing_on_noop:
                if (
                    reloaded is not None
                    and feeds_exist(root)
                    and material_catalog_digest(reloaded) == material_catalog_digest(catalog)
                ):
                    if overlay_clocks:
                        to_save = stamp_state_revision(
                            _overlay_observation_clocks(base=reloaded, clocks=catalog)
                        )
                        save_catalog(default_catalog_path(root), to_save)
                        logger.info(
                            "Post-lock material matches; overlaying clocks onto reloaded catalog"
                        )
                        return _LockedWrite(
                            catalog=to_save,
                            action=PipelineAction.STATE_CHANGED.value,
                        )
                    logger.info("Post-lock public bundle verified; no durable write")
                    return _LockedWrite(
                        catalog=reloaded,
                        action=PipelineAction.NO_CHANGE.value,
                    )
            elif _material_unchanged_vs_disk(
                root,
                catalog=catalog,
                rss=rss,
                atom=atom,
                json_feed=json_feed,
                simple_rss=simple_rss,
                simple_atom=simple_atom,
                simple_json_feed=simple_json_feed,
            ):
                if reloaded is not None:
                    to_save = stamp_state_revision(
                        _overlay_observation_clocks(base=reloaded, clocks=catalog)
                    )
                    save_catalog(default_catalog_path(root), to_save)
                    logger.info(
                        "Post-lock material matches; overlaying clocks onto reloaded catalog"
                    )
                    return _LockedWrite(
                        catalog=to_save,
                        action=PipelineAction.STATE_CHANGED.value,
                    )
                logger.warning("Post-lock feeds match but catalog.json is missing; publishing")
            else:
                logger.info(
                    "Post-lock disk material differs; publishing feeds and catalog together"
                )
        gen_id = _stage_and_materialize(
            root,
            catalog=catalog,
            rss=rss,
            atom=atom,
            json_feed=json_feed,
            simple_rss=simple_rss,
            simple_atom=simple_atom,
            simple_json_feed=simple_json_feed,
            reporter=reporter,
        )
        published = load_catalog(default_catalog_path(root))
        if published is None:
            raise FeedError(
                "Publication integrity: catalog.json missing after materialize; "
                f"generation {gen_id} did not land. Re-run to rebase."
            )
        if published.last_generation_id != gen_id:
            raise FeedError(
                "Publication integrity: catalog last_generation_id "
                f"{published.last_generation_id!r} != requested {gen_id!r}."
            )
        return _LockedWrite(
            catalog=published,
            action=PipelineAction.MATERIAL_CHANGED.value,
        )
    finally:
        release_write_lock(lock)


def _save_catalog_under_lock(
    root: Path,
    catalog: Catalog,
    *,
    rss: bytes,
    atom: bytes,
    json_feed: bytes,
    simple_rss: bytes,
    simple_atom: bytes,
    simple_json_feed: bytes,
    reporter: ProgressReporter | None = None,
    base_material_digest: str | None = None,
    base_state_revision: str | None = None,
) -> _LockedWrite:
    """Lock, recover, then overlay-save or publish in one protected sequence.

    The decisive disk comparison always runs after ``acquire_write_lock`` and
    ``recover_materialize`` (PGF-P0-001), including when recovery is a no-op.
    Matching material overlays this-run clocks onto the **reloaded** catalog
    (never the pre-lock object) only when ``state_revision`` still matches
    ``base_state_revision`` after recover (PGF-2026-030). Differing material
    publishes this run's feeds and catalog together **without** releasing the
    lock (RV-R-001 / RV-C-001) only when the durable digest still matches
    ``base_material_digest``.
    """
    return _finalize_under_lock(
        root,
        catalog,
        rss=rss,
        atom=atom,
        json_feed=json_feed,
        simple_rss=simple_rss,
        simple_atom=simple_atom,
        simple_json_feed=simple_json_feed,
        reporter=reporter or NULL_REPORTER,
        overlay_clocks=True,
        verify_existing_on_noop=False,
        force_publish=False,
        min_items=1,
        public_base_url=None,
        base_material_digest=base_material_digest,
        base_state_revision=base_state_revision,
    )


def _publish_catalog_and_feeds(
    root: Path,
    *,
    catalog: Catalog,
    rss: bytes,
    atom: bytes,
    json_feed: bytes,
    simple_rss: bytes,
    simple_atom: bytes,
    simple_json_feed: bytes,
    min_items: int,
    reporter: ProgressReporter,
    public_base_url: str | None = None,
    base_material_digest: str | None = None,
    base_state_revision: str | None = None,
) -> Catalog:
    """Verify in memory, then publish via locked staged generation.

    Stages all seven artifacts under ``.cache/generations/<id>/``, verifies the
    staged triple pair, then materializes public ``feeds/*`` + ``catalog.json``
    under an exclusive writer lock. Crash recovery via
    :func:`recover_materialize` runs only while the writer lock is held
    (H-01/H-02 / RV-R-001). Staged MANIFEST digests are re-checked before
    public writes (RV-R-003).
    """
    assert_verified(
        rss=rss,
        atom=atom,
        json_feed=json_feed,
        min_items=min_items,
        kind="enriched",
        public_base_url=public_base_url,
    )
    assert_verified(
        rss=simple_rss,
        atom=simple_atom,
        json_feed=simple_json_feed,
        min_items=min_items,
        kind="simple",
        public_base_url=public_base_url,
    )
    committed = _finalize_under_lock(
        root,
        catalog,
        rss=rss,
        atom=atom,
        json_feed=json_feed,
        simple_rss=simple_rss,
        simple_atom=simple_atom,
        simple_json_feed=simple_json_feed,
        reporter=reporter,
        overlay_clocks=True,
        verify_existing_on_noop=False,
        force_publish=True,
        min_items=min_items,
        public_base_url=public_base_url,
        base_material_digest=base_material_digest,
        base_state_revision=base_state_revision,
    )
    catalog_path = default_catalog_path(root)
    logger.info(
        "Published feeds + catalog → {} + {}",
        root / "feeds",
        catalog_path,
    )
    return committed.catalog


def _essays_from_catalog(catalog: Catalog) -> list[Essay]:
    """Rebuild Essay list from catalog ``entry_order`` (304 plan-only path)."""
    essays: list[Essay] = []
    position = 1
    for stable_id in catalog.entry_order:
        entry = catalog.entries.get(stable_id)
        if entry is None:
            continue
        essays.append(
            Essay(
                position=position,
                title=entry.title,
                url=entry.url,
                stable_id=entry.stable_id,
                is_permalink=True,
                summary=entry.summary,
                published_hint=entry.published_hint,
                published_at=entry.published_at,
                content_hash=entry.page.raw_sha256,
            )
        )
        position += 1
    return essays


def _load_index_validators(root: Path, catalog: Catalog) -> tuple[str | None, str | None]:
    """Prefer durable catalog validators; fall back to gitignored http-cache."""
    etag = catalog.index.etag
    last_modified = catalog.index.last_modified
    if etag or last_modified:
        return etag, last_modified
    cache_path = root / _HTTP_CACHE_REL
    if not cache_path.is_file():
        return None, None
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return None, None
    if not isinstance(data, dict):
        return None, None
    cache_etag = data.get("etag")
    cache_lm = data.get("last_modified")
    return (
        cache_etag if isinstance(cache_etag, str) else None,
        cache_lm if isinstance(cache_lm, str) else None,
    )


def _persist_http_cache(
    root: Path,
    *,
    etag: str | None,
    last_modified: str | None,
    status_code: int | None,
) -> None:
    """Write gitignored validator sidecar under ``.cache/`` (safe on 304 paths)."""
    path = root / _HTTP_CACHE_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "etag": etag,
        "last_modified": last_modified,
        "status_code": status_code,
    }
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def run_catalog_pipeline(
    settings: Settings,
    *,
    source_file: Path | None = None,
    html: str | None = None,
    reporter: ProgressReporter | None = None,
    now: datetime | None = None,
    from_feeds: bool = False,
) -> PipelineResult:
    """Execute the durable-catalog update pipeline under ``settings.repo_root``."""
    progress = reporter or NULL_REPORTER
    observed = require_aware_utc(now) if now is not None else utc_now()
    root = settings.repo_root
    fingerprint = _material_config_fingerprint(settings)

    if from_feeds:
        # Bootstrap in memory only; durable catalog is written only after the
        # full pipeline succeeds (H-12 — no early save_catalog).
        prior = bootstrap_catalog_from_feeds(
            root,
            now=observed,
            material_config_fingerprint=fingerprint,
        )
        logger.info(
            "Bootstrapped catalog from feeds/ in memory ({} entries)",
            len(prior.entry_order),
        )
    else:
        prior = _load_or_bootstrap_catalog(root, now=observed, fingerprint=fingerprint)
    # Digest of the durable catalog this candidate is based on (PGF-2026-002).
    # Capture before fingerprint mutation so a settings bump is not treated
    # as a concurrent writer.
    base_material_digest = material_catalog_digest(prior)
    base_state_revision = prior.state_revision
    fingerprint_changed = prior.material_config_fingerprint != fingerprint
    if fingerprint_changed:
        prior = prior.model_copy(update={"material_config_fingerprint": fingerprint})

    index_not_modified = False
    index_etag: str | None = None
    index_last_modified: str | None = None
    index_status: int | None = 200
    index_raw_sha256: str | None = None
    index_decoded_sha256: str | None = None
    index_raw_bytes: int | None = None
    index_decoded_bytes: int | None = None
    index_selected_encoding: str | None = None

    if html is not None:
        index_html: str | None = html
    elif source_file is not None:
        logger.info("Reading local HTML {}", source_file)
        index_html, index_selected_encoding = _read_source_file(
            source_file, max_bytes=settings.max_bytes
        )
    else:
        etag, last_modified = _load_index_validators(root, prior)
        logger.info("Fetching {}", settings.source_url)
        fetched = fetch_index(
            settings.source_url,
            timeout=settings.timeout,
            retries=settings.retries,
            max_bytes=settings.max_bytes,
            etag=etag,
            last_modified=last_modified,
            prior_body_hash=prior.index.raw_sha256,
        )
        index_not_modified = fetched.not_modified
        index_etag = fetched.etag
        index_last_modified = fetched.last_modified
        index_status = fetched.status_code
        index_raw_sha256 = fetched.raw_sha256
        index_decoded_sha256 = fetched.decoded_sha256
        index_raw_bytes = fetched.raw_bytes_received
        index_decoded_bytes = fetched.decoded_bytes_received
        index_selected_encoding = fetched.selected_encoding
        if fetched.not_modified:
            index_html = None
        elif fetched.html is None:
            raise FeedError("Index fetch returned no body")
        else:
            index_html = fetched.html
        _persist_http_cache(
            root,
            etag=index_etag,
            last_modified=index_last_modified,
            status_code=index_status,
        )

    if index_not_modified:
        # Acceptable 304 only: fetch_index.not_modified (AUD-016). Never treat a
        # missing body as plan-only unless the fetch reported not_modified.
        logger.info("Index not modified (304); plan-only refresh path")
        essays = _essays_from_catalog(prior)
        catalog = prior
        changeset = ChangeSet()
        index_hash = (
            prior.index.decoded_sha256
            or prior.index.raw_sha256
            or content_sha256("304-not-modified")
        )
        index_raw_sha256 = prior.index.raw_sha256
        index_decoded_sha256 = prior.index.decoded_sha256
        index_selected_encoding = prior.index.selected_encoding
    else:
        if index_html is None:
            raise FeedError("Index fetch returned no body")
        index_hash = content_sha256(index_html)
        from paul_graham_essay_feeds.discover import evaluate_discovery_anomaly

        allow_fallback = (
            settings.allow_discovery_fallback
            if prior.entry_order
            else settings.allow_bootstrap_fallback
        )
        discovered, discovery_report = discover_essays(
            index_html,
            base_url=settings.source_url,
            min_items=settings.min_items,
            allow_fallback=allow_fallback,
        )
        quarantine = evaluate_discovery_anomaly(
            set(prior.entry_order),
            {item.stable_id for item in discovered},
            report=discovery_report,
        )
        if quarantine is not None:
            raise FeedError(f"Discovery quarantined: {quarantine}")
        catalog, changeset = reconcile_discovery(prior, discovered, now=observed)
        essays = [discovery_item_to_essay(item) for item in discovered]

    index_fingerprint = _index_identity_fingerprint(essays)

    plan = plan_refresh(
        catalog,
        force=settings.force,
        enrich=settings.enrich,
        stale_after_days=settings.stale_after_days,
        now=observed,
        max_page_fetches=settings.max_page_fetches,
    )
    skip_network = (not fingerprint_changed) and _should_skip_publish(
        root=root, force=settings.force, changeset=changeset, plan=plan
    )
    due_ids = {d.stable_id for d in plan.decisions if d.fetch_page}
    # Skip-network omits enrich GETs; dedicated probes still run independently
    # when validate_links is on (PGF-2026-005).
    enrich_ids = due_ids if settings.enrich and not skip_network else set()
    attempted_decisions = [
        RefreshDecision(
            stable_id=d.stable_id,
            fetch_page=d.stable_id in enrich_ids,
            reasons=d.reasons,
        )
        for d in plan.decisions
    ]
    # Persist cursor from IDs actually attempted this run (audit P2-14).
    catalog = catalog_with_page_fetch_cursor(catalog, attempted_decisions)
    link_cursor_next: str | None = None
    links_checked = 0
    links_skipped = 0
    links_healthy = 0
    links_failed = 0
    links_failed_ids: tuple[str, ...] = ()
    host_cooldown: HostCooldown | None = None
    all_due_use_enrich_get = False

    if skip_network:
        logger.info(
            "Catalog refresh not due (hash {}); skipping enrich/page fetches",
            index_hash[:12],
        )

    if settings.validate_links or (not skip_network and settings.enrich and due_ids):
        host_cooldown = HostCooldown(settings.host_cooldown_seconds)

    probe_essays: list[Essay] = []
    prev_link_cursor = 0
    next_link_cursor = 0
    if settings.validate_links:
        eligible_probes = (
            [e for e in essays if e.stable_id not in enrich_ids] if enrich_ids else list(essays)
        )
        prev_link_cursor = _version_cursor(catalog.versions, _LINK_VALIDATION_CURSOR_KEY)
        probe_essays, next_link_cursor = _rotate_probe_essays(
            eligible_probes,
            cursor=prev_link_cursor,
            limit=settings.max_link_validations,
        )
        links_checked = len(probe_essays)
        links_skipped = max(0, len(eligible_probes) - links_checked)
        all_due_use_enrich_get = bool(enrich_ids) and not probe_essays

    logger.info(
        "Planned requests: {} page fetches (cap {}), {} dedicated link probes (cap {})",
        len(enrich_ids),
        budget_label(settings.max_page_fetches),
        links_checked,
        budget_label(settings.max_link_validations),
    )

    if settings.validate_links:
        if enrich_ids and probe_essays:
            logger.info(
                "Live-checking {} URLs not enriched this run…",
                len(probe_essays),
            )
        elif all_due_use_enrich_get:
            logger.info(
                "Checking and enriching {} essay pages (one GET each)…",
                len(essays),
            )
        elif skip_network and probe_essays:
            logger.info("Live-checking {} URLs (link-validation phase)…", len(probe_essays))
        probe_report = validate_essays_live(
            probe_essays,
            timeout=settings.link_timeout,
            retries=settings.retries,
            workers=settings.link_workers,
            max_bytes=settings.max_bytes,
            quiet=settings.quiet,
            host_cooldown_seconds=settings.host_cooldown_seconds,
            host_cooldown=host_cooldown,
        )
        if not isinstance(probe_report, LinkProbeReport):
            probe_report = LinkProbeReport(checked=len(probe_essays), ok=0, failures=())
        links_healthy = probe_report.ok
        links_failed = probe_report.failed
        links_failed_ids = probe_report.failures
        if probe_essays and next_link_cursor != prev_link_cursor:
            link_cursor_next = str(next_link_cursor)

    if not skip_network:
        if settings.enrich and due_ids:
            due_essays = _essays_for_ids(essays, due_ids)
            if not (settings.validate_links and all_due_use_enrich_get):
                logger.info(
                    "Enriching {}/{} essays selected by refresh plan…",
                    len(due_essays),
                    len(essays),
                )
            page_validators = {}
            for sid in due_ids:
                entry = catalog.entries.get(sid)
                if entry is None:
                    continue
                if entry.page.last_error_kind == "parse":
                    page_validators[sid] = (None, None)
                else:
                    page_validators[sid] = (entry.page.etag, entry.page.last_modified)
            page_evidence: dict[str, PageEnrichEvidence] = {}
            enriched = enrich_essays(
                due_essays,
                timeout=settings.enrich_timeout,
                workers=settings.enrich_workers,
                retries=settings.retries,
                max_bytes=settings.max_bytes,
                quiet=settings.quiet,
                page_validators=page_validators,
                page_evidence_out=page_evidence,
                host_cooldown_seconds=settings.host_cooldown_seconds,
                host_cooldown=host_cooldown,
            )
            catalog = _apply_enrichment(
                catalog,
                enriched,
                now=observed,
                page_evidence=page_evidence,
            )
        elif settings.enrich and not due_ids:
            logger.info("Refresh plan: no page fetches due")

        # Stamp complete schema-v2 index evidence (PGF-P1-003). Never invent raw
        # from decoded when transport did not provide raw (RV-R-004).
        index_state = _complete_index_state(
            prior=catalog.index,
            observed=observed,
            etag=index_etag,
            last_modified=index_last_modified,
            raw_sha256=index_raw_sha256,
            decoded_sha256=index_decoded_sha256 or index_hash,
            status_code=index_status,
            raw_bytes_received=index_raw_bytes,
            decoded_bytes_received=index_decoded_bytes,
            selected_encoding=index_selected_encoding,
        )
        next_versions = {
            **dict(catalog.versions),
            "generator": GENERATOR,
            "package": __version__,
        }
        if link_cursor_next is not None:
            next_versions[_LINK_VALIDATION_CURSOR_KEY] = link_cursor_next
        catalog = catalog.model_copy(
            update={
                "index": index_state,
                "versions": next_versions,
            }
        )
    elif link_cursor_next is not None:
        versions = dict(catalog.versions)
        versions[_LINK_VALIDATION_CURSOR_KEY] = link_cursor_next
        catalog = catalog.model_copy(update={"versions": versions})

    snapshot = catalog_to_feed_snapshot(
        catalog,
        generator=GENERATOR,
        public_base_url=settings.public_base_url,
        index_hash=index_hash,
        index_fingerprint=index_fingerprint,
        summary_mode="enriched",
    )
    rss, atom, json_feed = render_snapshot_feeds(snapshot)
    simple_snapshot = catalog_to_feed_snapshot(
        catalog,
        generator=GENERATOR,
        public_base_url=settings.public_base_url,
        index_hash=index_hash,
        index_fingerprint=index_fingerprint,
        summary_mode="title_only",
    )
    simple_rss, simple_atom, simple_json_feed = render_snapshot_feeds(simple_snapshot)

    # Verify in memory before any durable publish decision.
    assert_verified(
        rss=rss,
        atom=atom,
        json_feed=json_feed,
        min_items=settings.min_items,
        kind="enriched",
        public_base_url=settings.public_base_url,
    )
    assert_verified(
        rss=simple_rss,
        atom=simple_atom,
        json_feed=simple_json_feed,
        min_items=settings.min_items,
        kind="simple",
        public_base_url=settings.public_base_url,
    )

    if skip_network and link_cursor_next is None:
        committed = _finalize_under_lock(
            root,
            catalog,
            rss=rss,
            atom=atom,
            json_feed=json_feed,
            simple_rss=simple_rss,
            simple_atom=simple_atom,
            simple_json_feed=simple_json_feed,
            reporter=progress,
            overlay_clocks=False,
            verify_existing_on_noop=True,
            force_publish=False,
            min_items=settings.min_items,
            public_base_url=settings.public_base_url,
            base_material_digest=base_material_digest,
            base_state_revision=base_state_revision,
        )
        return _result_for_locked_write(
            committed,
            changeset=changeset,
            refresh_plan=plan,
            index_hash=index_hash,
            essay_count=len(essays),
            links_checked=links_checked,
            links_skipped=links_skipped,
            links_healthy=links_healthy,
            links_failed=links_failed,
            links_failed_ids=links_failed_ids,
        )

    # Post-enrich material-noop: feed bytes match disk, but page clocks may have
    # advanced in memory — persist catalog only so freshness gates work next run.
    if not settings.force and _material_unchanged_vs_disk(
        root,
        catalog=catalog,
        rss=rss,
        atom=atom,
        json_feed=json_feed,
        simple_rss=simple_rss,
        simple_atom=simple_atom,
        simple_json_feed=simple_json_feed,
    ):
        logger.info(
            "Post-enrich material unchanged (hash {}); committing under writer lock",
            index_hash[:12],
        )
        committed = _save_catalog_under_lock(
            root,
            catalog,
            rss=rss,
            atom=atom,
            json_feed=json_feed,
            simple_rss=simple_rss,
            simple_atom=simple_atom,
            simple_json_feed=simple_json_feed,
            reporter=progress,
            base_material_digest=base_material_digest,
            base_state_revision=base_state_revision,
        )
        return _result_for_locked_write(
            committed,
            changeset=changeset,
            refresh_plan=plan,
            index_hash=index_hash,
            essay_count=len(essays),
            links_checked=links_checked,
            links_skipped=links_skipped,
            links_healthy=links_healthy,
            links_failed=links_failed,
            links_failed_ids=links_failed_ids,
        )

    published = _publish_catalog_and_feeds(
        root,
        catalog=catalog,
        rss=rss,
        atom=atom,
        json_feed=json_feed,
        simple_rss=simple_rss,
        simple_atom=simple_atom,
        simple_json_feed=simple_json_feed,
        min_items=settings.min_items,
        reporter=progress,
        public_base_url=settings.public_base_url,
        base_material_digest=base_material_digest,
        base_state_revision=base_state_revision,
    )

    return PipelineResult(
        catalog=published,
        changeset=changeset,
        refresh_plan=plan,
        index_hash=index_hash,
        essay_count=len(essays),
        skipped=False,
        action=PipelineAction.MATERIAL_CHANGED.value,
        changed_paths=_MATERIAL_CHANGED_PATHS,
        links_checked=links_checked,
        links_skipped=links_skipped,
        links_healthy=links_healthy,
        links_failed=links_failed,
        links_failed_ids=links_failed_ids,
    )
