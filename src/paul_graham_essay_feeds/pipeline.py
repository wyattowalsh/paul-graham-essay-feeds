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
from pathlib import Path

from loguru import logger

from paul_graham_essay_feeds import __version__
from paul_graham_essay_feeds.catalog import (
    ChangeSet,
    RefreshPlan,
    bootstrap_catalog_from_feeds,
    default_catalog_path,
    load_catalog,
    plan_refresh,
    reconcile_discovery,
    save_catalog,
)
from paul_graham_essay_feeds.discovery import discover_essays
from paul_graham_essay_feeds.enrich import PageEnrichEvidence, enrich_essays, validate_essays_live
from paul_graham_essay_feeds.feeds import (
    catalog_to_feed_snapshot,
    feed_paths,
    feeds_exist,
    render_snapshot_feeds,
    write_feeds,
)
from paul_graham_essay_feeds.http import decode_html, fetch_index
from paul_graham_essay_feeds.models import (
    GENERATOR,
    NULL_REPORTER,
    Catalog,
    CatalogEntry,
    Essay,
    FeedError,
    Lifecycle,
    ProgressReporter,
    ResourceState,
    content_sha256,
    discovery_item_to_essay,
    require_aware_utc,
    utc_now,
)
from paul_graham_essay_feeds.settings import Settings
from paul_graham_essay_feeds.verify import assert_verified

_HTTP_CACHE_REL: Path = Path(".cache") / "http-cache.json"


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """Outcome of one catalog-pipeline update pass."""

    catalog: Catalog
    changeset: ChangeSet
    refresh_plan: RefreshPlan
    index_hash: str
    essay_count: int
    skipped: bool
    action: str  # "updated" | "unchanged"


def _read_source_file(path: Path, *, max_bytes: int) -> str:
    try:
        size = path.stat().st_size
    except OSError:
        size = -1
    if size > max_bytes:
        raise FeedError(f"Source file over {max_bytes} bytes: {path}")
    raw = path.read_bytes()
    if len(raw) > max_bytes:
        raise FeedError(f"Source file over {max_bytes} bytes: {path}")
    return decode_html(raw)


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


def _essays_for_ids(essays: list[Essay], ids: set[str]) -> list[Essay]:
    return [e for e in essays if e.stable_id in ids]


def _summary_quality(summary: str | None) -> float | None:
    if summary is None or not summary.strip():
        return None
    if "\ufffd" in summary:
        return 0.35
    return 0.9


def _apply_enrichment(
    catalog: Catalog,
    enriched: list[Essay],
    *,
    now: datetime,
    page_evidence: Mapping[str, PageEnrichEvidence] | None = None,
) -> Catalog:
    """Merge enrichment fields into catalog entries (preserve prior-good).

    HTTP 304 retains prior summaries (no empty-body replace). ETag /
    Last-Modified land on ``page`` ResourceState from 200 evidence; they only
    hit durable catalog storage when material publish saves the catalog.
    """
    by_id = {e.stable_id: e for e in enriched}
    evidence_by_id = page_evidence or {}
    next_entries: dict[str, CatalogEntry] = dict(catalog.entries)
    for stable_id, essay in by_id.items():
        entry = next_entries.get(stable_id)
        if entry is None:
            continue
        ev = evidence_by_id.get(stable_id)
        if ev is not None and ev.not_modified:
            # 304: retain prior-good / summary; update check clock only.
            page = ResourceState(
                etag=ev.etag or entry.page.etag,
                last_modified=ev.last_modified or entry.page.last_modified,
                raw_sha256=entry.page.raw_sha256,
                decoded_sha256=entry.page.decoded_sha256,
                last_checked_at=now,
                status_code=304,
                selected_encoding=entry.page.selected_encoding,
            )
            next_entries[stable_id] = entry.model_copy(update={"page": page})
            continue

        new_summary = essay.summary if essay.summary and essay.summary.strip() else None
        prior_good = entry.prior_good_summary
        if (new_summary is not None and "\ufffd" not in new_summary) or (
            prior_good is None and new_summary is not None
        ):
            prior_good = new_summary

        # Prefer prior-good when the new scrape is empty or replacement-char heavy.
        effective = new_summary
        if (effective is None and prior_good is not None) or (
            effective is not None
            and "\ufffd" in effective
            and prior_good is not None
            and "\ufffd" not in prior_good
        ):
            effective = prior_good

        material = (
            (effective or "") != (entry.summary or "")
            or (essay.published_hint or None) != (entry.published_hint or None)
            or essay.published_at != entry.published_at
        )
        # Persist validators on 200 evidence; otherwise keep prior page validators.
        if ev is not None and ev.status_code == 200:
            page_etag = ev.etag
            page_last_modified = ev.last_modified
            page_status = 200
        else:
            page_etag = entry.page.etag
            page_last_modified = entry.page.last_modified
            page_status = 200 if essay.content_hash else entry.page.status_code
        page = ResourceState(
            etag=page_etag,
            last_modified=page_last_modified,
            raw_sha256=essay.content_hash or entry.page.raw_sha256,
            decoded_sha256=essay.content_hash or entry.page.decoded_sha256,
            last_checked_at=now,
            status_code=page_status,
        )
        next_entries[stable_id] = entry.model_copy(
            update={
                "summary": effective,
                "summary_source": "page" if new_summary else entry.summary_source,
                "summary_quality": _summary_quality(effective),
                "prior_good_summary": prior_good,
                "published_hint": essay.published_hint or entry.published_hint,
                "published_at": essay.published_at or entry.published_at,
                "page": page,
                "observed_updated_at": now if material else entry.observed_updated_at,
            }
        )
    return catalog.model_copy(update={"entries": next_entries})


def _with_index_state(catalog: Catalog, *, index_hash: str, now: datetime) -> Catalog:
    return catalog.model_copy(
        update={
            "index": ResourceState(
                raw_sha256=index_hash,
                decoded_sha256=index_hash,
                last_checked_at=now,
                status_code=200,
            ),
            "versions": {
                **dict(catalog.versions),
                "generator": GENERATOR,
                "package": __version__,
            },
        }
    )


def material_catalog_digest(catalog: Catalog) -> str:
    """Hash material catalog fields only (exclude volatile observation clocks)."""
    material_entries: dict[str, dict[str, object]] = {}
    for stable_id, entry in catalog.entries.items():
        material_entries[stable_id] = {
            "stable_id": entry.stable_id,
            "url": entry.url,
            "title": entry.title,
            "position": entry.position,
            "lifecycle": entry.lifecycle.value,
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
            "summary_quality": entry.summary_quality,
            "prior_good_summary": entry.prior_good_summary,
            "page_raw_sha256": entry.page.raw_sha256,
        }
    payload = {
        "schema_version": catalog.schema_version,
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
    """Skip rewrite when reconcile is inert and no page work is due (F-001).

    Missing root ``catalog.json`` or ``feeds/`` always publishes once.
    """
    if force:
        return False
    if not feeds_exist(root):
        return False
    if not default_catalog_path(root).is_file():
        return False
    if changeset.added or changeset.updated or changeset.tombstone_candidates:
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
) -> bool:
    """True when post-enrich material matches on-disk catalog + feeds."""
    if not feeds_exist(root):
        return False
    existing = load_catalog(default_catalog_path(root))
    if existing is None:
        return False
    if material_catalog_digest(existing) != material_catalog_digest(catalog):
        return False
    paths = feed_paths(root)
    return (
        _feed_bytes_match(paths["rss"], rss)
        and _feed_bytes_match(paths["atom"], atom)
        and _feed_bytes_match(paths["json"], json_feed)
    )


def _publish_catalog_and_feeds(
    root: Path,
    *,
    catalog: Catalog,
    rss: bytes,
    atom: bytes,
    json_feed: bytes,
    min_items: int,
    reporter: ProgressReporter,
) -> Catalog:
    """Verify in memory, then atomically write root catalog.json + feeds/*."""
    assert_verified(
        rss=rss,
        atom=atom,
        json_feed=json_feed,
        min_items=min_items,
    )
    catalog_path = default_catalog_path(root)
    save_catalog(catalog_path, catalog)
    write_feeds(
        root,
        rss=rss,
        atom=atom,
        json_feed=json_feed,
        reporter=reporter,
    )
    logger.info("Published catalog + feeds → {} + {}", catalog_path, root / "feeds")
    return catalog


def _essays_from_catalog(catalog: Catalog) -> list[Essay]:
    """Rebuild Essay list from active catalog entries (304 plan-only path)."""
    essays: list[Essay] = []
    position = 1
    for stable_id in catalog.entry_order:
        entry = catalog.entries.get(stable_id)
        if entry is None or entry.lifecycle is Lifecycle.TOMBSTONED:
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
        # Force bootstrap from feeds/ even when a durable catalog already exists.
        prior = bootstrap_catalog_from_feeds(
            root,
            now=observed,
            material_config_fingerprint=fingerprint,
        )
        if prior.material_config_fingerprint in {"bootstrap", "default"}:
            prior = prior.model_copy(update={"material_config_fingerprint": fingerprint})
        save_catalog(default_catalog_path(root), prior)
        logger.info(
            "Bootstrapped catalog from feeds/ ({} entries)",
            len(prior.entry_order),
        )
    else:
        prior = _load_or_bootstrap_catalog(root, now=observed, fingerprint=fingerprint)
        # Align fingerprint when bootstrapping empty fingerprint catalogs.
        if prior.material_config_fingerprint in {"bootstrap", "default"}:
            prior = prior.model_copy(update={"material_config_fingerprint": fingerprint})

    index_not_modified = False
    index_etag: str | None = None
    index_last_modified: str | None = None
    index_status: int | None = 200

    if html is not None:
        index_html: str | None = html
    elif source_file is not None:
        logger.info("Reading local HTML {}", source_file)
        index_html = _read_source_file(source_file, max_bytes=settings.max_bytes)
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
        )
        index_not_modified = fetched.not_modified
        index_etag = fetched.etag
        index_last_modified = fetched.last_modified
        index_status = fetched.status_code
        index_html = fetched.html
        _persist_http_cache(
            root,
            etag=index_etag,
            last_modified=index_last_modified,
            status_code=index_status,
        )

    if index_not_modified or index_html is None:
        # 304 / plan-only: no discovery without a body.
        logger.info("Index not modified (304); plan-only refresh path")
        essays = _essays_from_catalog(prior)
        catalog = prior
        changeset = ChangeSet()
        index_hash = (
            prior.index.decoded_sha256
            or prior.index.raw_sha256
            or content_sha256("304-not-modified")
        )
    else:
        index_hash = content_sha256(index_html)
        discovered, _report = discover_essays(
            index_html,
            base_url=settings.source_url,
            min_items=settings.min_items,
            allow_fallback=settings.allow_discovery_fallback,
        )
        catalog, changeset = reconcile_discovery(prior, discovered, now=observed)
        essays = [discovery_item_to_essay(item) for item in discovered]

    index_fingerprint = _index_identity_fingerprint(essays)

    plan = plan_refresh(
        catalog,
        force=settings.force,
        enrich=settings.enrich,
        stale_after_days=settings.stale_after_days,
        now=observed,
    )

    if _should_skip_publish(root=root, force=settings.force, changeset=changeset, plan=plan):
        # Pre-enrich UNCHANGED: zero tracked writes (no save_catalog / enrich / publish).
        # Page-due plans never reach here (F-001); identity+planner decide skip.
        # http-cache sidecar may update above; tracked paths stay untouched.
        # from_feeds already persisted the bootstrapped catalog above.
        logger.info(
            "Catalog refresh not due (hash {}); skipping enrich/write",
            index_hash[:12],
        )
        return PipelineResult(
            catalog=catalog,
            changeset=changeset,
            refresh_plan=plan,
            index_hash=index_hash,
            essay_count=len(essays),
            skipped=True,
            action="unchanged",
        )

    due_ids = {d.stable_id for d in plan.decisions if d.fetch_page}
    # URLs we will GET for enrich this run — a successful enrich implies reachability,
    # so live HEAD probes skip those ids (probe the rest first, then enrich).
    enrich_ids = due_ids if settings.enrich else set()

    if settings.validate_links:
        probe_essays = (
            [e for e in essays if e.stable_id not in enrich_ids] if enrich_ids else essays
        )
        if enrich_ids and probe_essays:
            logger.info(
                "Live-probing {}/{} essays not selected for enrich this run…",
                len(probe_essays),
                len(essays),
            )
        elif enrich_ids and not probe_essays:
            logger.info(
                "Skipping dedicated link probes (all {} essays due for enrich GET)",
                len(essays),
            )
        validate_essays_live(
            probe_essays,
            timeout=settings.link_timeout,
            retries=settings.retries,
            workers=settings.link_workers,
            max_bytes=settings.max_bytes,
            quiet=settings.quiet,
        )

    if settings.enrich and due_ids:
        due_essays = _essays_for_ids(essays, due_ids)
        logger.info(
            "Enriching {}/{} essays selected by refresh plan…",
            len(due_essays),
            len(essays),
        )
        page_validators = {
            sid: (
                catalog.entries[sid].page.etag,
                catalog.entries[sid].page.last_modified,
            )
            for sid in due_ids
            if sid in catalog.entries
        }
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
        )
        catalog = _apply_enrichment(
            catalog,
            enriched,
            now=observed,
            page_evidence=page_evidence,
        )
    elif settings.enrich and not due_ids:
        logger.info("Refresh plan: no page fetches due")

    # Stamp index validators into catalog only when we continue toward publish.
    index_state = ResourceState(
        etag=index_etag if index_etag is not None else catalog.index.etag,
        last_modified=(
            index_last_modified if index_last_modified is not None else catalog.index.last_modified
        ),
        raw_sha256=index_hash,
        decoded_sha256=index_hash,
        last_checked_at=observed,
        status_code=index_status if index_status is not None else catalog.index.status_code,
    )
    catalog = catalog.model_copy(
        update={
            "index": index_state,
            "versions": {
                **dict(catalog.versions),
                "generator": GENERATOR,
                "package": __version__,
            },
        }
    )
    snapshot = catalog_to_feed_snapshot(
        catalog,
        generator=GENERATOR,
        public_base_url=settings.public_base_url,
        index_hash=index_hash,
        index_fingerprint=index_fingerprint,
    )
    rss, atom, json_feed = render_snapshot_feeds(snapshot)

    # Verify in memory before any durable publish decision.
    assert_verified(
        rss=rss,
        atom=atom,
        json_feed=json_feed,
        min_items=settings.min_items,
    )

    # Post-enrich UNCHANGED: material digest + feed bytes match disk → skip.
    if not settings.force and _material_unchanged_vs_disk(
        root,
        catalog=catalog,
        rss=rss,
        atom=atom,
        json_feed=json_feed,
    ):
        logger.info(
            "Post-enrich material unchanged (hash {}); skipping publish",
            index_hash[:12],
        )
        return PipelineResult(
            catalog=catalog,
            changeset=changeset,
            refresh_plan=plan,
            index_hash=index_hash,
            essay_count=len(essays),
            skipped=True,
            action="unchanged",
        )

    published = _publish_catalog_and_feeds(
        root,
        catalog=catalog,
        rss=rss,
        atom=atom,
        json_feed=json_feed,
        min_items=settings.min_items,
        reporter=progress,
    )

    return PipelineResult(
        catalog=published,
        changeset=changeset,
        refresh_plan=plan,
        index_hash=index_hash,
        essay_count=len(essays),
        skipped=False,
        action="updated",
    )
