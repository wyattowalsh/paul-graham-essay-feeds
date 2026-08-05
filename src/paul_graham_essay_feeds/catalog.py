"""Durable catalog behavior: I/O, bootstrap, reconcile, and refresh planning.

Combines atomic catalog storage (root ``catalog.json``), discovery reconciliation,
and deterministic page/index refresh planning. Atomic file writes live here so
feed projection and catalog persistence share one helper.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import ValidationError

from paul_graham_essay_feeds.models import (
    Catalog,
    CatalogEntry,
    ConfigurationError,
    DiscoveryItem,
    FeedError,
    require_aware_utc,
)

DEFAULT_CATALOG_REL: Path = Path("catalog.json")
"""Default catalog path relative to a repository root (repo-root SSOT)."""

CATALOG_SCHEMA_VERSION: Final[Literal[2]] = 2
"""Current durable catalog schema version written by this module."""

_FILE_MODE: int = 0o644
_DEFAULT_SCHEMA_VERSION: Final[Literal[2]] = 2
_DEFAULT_FINGERPRINT = "default"
_MAX_FAILURE_BACKOFF_DAYS: Final[int] = 7


# ---------------------------------------------------------------------------
# Atomic file I/O
# ---------------------------------------------------------------------------


def atomic_write_bytes(path: Path, data: bytes, *, mode: int = _FILE_MODE) -> None:
    """Atomically write ``data`` to ``path`` (mkstemp → write → fsync → chmod → replace).

    The temporary file is created in the same directory as ``path`` so
    ``os.replace`` is atomic on the same filesystem. On failure the temp is
    unlinked and the previous file (if any) is retained.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def atomic_write_text(
    path: Path,
    text: str,
    *,
    mode: int = _FILE_MODE,
    encoding: str = "utf-8",
) -> None:
    """Atomically write ``text`` to ``path`` as UTF-8 (or ``encoding``) bytes."""
    atomic_write_bytes(path, text.encode(encoding), mode=mode)


# ---------------------------------------------------------------------------
# Catalog I/O + bootstrap
# ---------------------------------------------------------------------------


def default_catalog_path(root: Path) -> Path:
    """Return the default catalog path under ``root`` (``catalog.json``)."""
    return Path(root) / DEFAULT_CATALOG_REL


def empty_catalog(
    *,
    material_config_fingerprint: str,
    versions: dict[str, str] | None = None,
) -> Catalog:
    """Return a new empty schema-v2 catalog with the given fingerprint."""
    return Catalog(
        schema_version=CATALOG_SCHEMA_VERSION,
        material_config_fingerprint=material_config_fingerprint,
        versions=dict(versions) if versions is not None else {},
    )


def bootstrap_catalog_from_feeds(
    root: Path,
    *,
    now: datetime,
    material_config_fingerprint: str = "bootstrap",
) -> Catalog:
    """Build a catalog from ``root/feeds/feed.json`` if it exists.

    Missing ``feed.json`` → empty catalog. Items become catalog entries with
    observation clocks set to ``now`` (never invent 1970). Non-empty summaries
    copy into both ``summary`` and ``prior_good_summary``.
    """
    observed_at = require_aware_utc(now)
    path = root / "feeds" / "feed.json"
    if not path.is_file():
        return empty_catalog(material_config_fingerprint=material_config_fingerprint)

    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"Unable to read bootstrap feed.json: {exc}") from exc

    if not isinstance(payload, dict):
        raise ConfigurationError("feed.json root must be an object")
    raw_items = payload.get("items")
    if raw_items is None:
        raw_items = []
    if not isinstance(raw_items, list):
        raise ConfigurationError("feed.json missing items array")

    entry_order: list[str] = []
    entries: dict[str, CatalogEntry] = {}
    position = 0
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue
        item_id = _nonempty_str(raw_item.get("id"))
        url = _nonempty_str(raw_item.get("url"))
        title = _nonempty_str(raw_item.get("title"))
        if item_id is None or url is None or title is None:
            continue
        if item_id in entries:
            continue

        summary = _nonempty_str(raw_item.get("summary"))
        entries[item_id] = CatalogEntry(
            stable_id=item_id,
            url=url,
            title=title,
            position=position,
            first_seen_at=observed_at,
            last_seen_at=observed_at,
            observed_updated_at=observed_at,
            summary=summary,
            prior_good_summary=summary,
        )
        entry_order.append(item_id)
        position += 1

    return Catalog(
        schema_version=CATALOG_SCHEMA_VERSION,
        material_config_fingerprint=material_config_fingerprint,
        entry_order=entry_order,
        entries=entries,
    )


def _nonempty_str(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text if text else None


def catalog_to_json(catalog: Catalog) -> str:
    """Serialize ``catalog`` to deterministic JSON (sorted keys, trailing newline)."""
    payload = catalog.model_dump(mode="json")
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def _migrate_resource_state_v1_to_v2(raw: dict[str, Any]) -> dict[str, Any]:
    """Map schema-v1 ResourceState clocks onto v2 lifecycle fields."""
    page = dict(raw)
    last_checked = page.get("last_checked_at")
    status = page.get("status_code")
    # Treat prior last_checked_at as success only when status looks successful
    # (200/304) or status is unknown (legacy rows often omitted status).
    success_like = status in (None, 200, 304)
    if last_checked is not None and success_like:
        page.setdefault("last_success_at", last_checked)
        page.setdefault("last_attempted_at", last_checked)
        page.setdefault("last_response_at", last_checked)
    elif last_checked is not None:
        page.setdefault("last_attempted_at", last_checked)
        page.setdefault("last_response_at", last_checked)
    page.setdefault("failure_count", 0)
    return page


def migrate_catalog(data: dict[str, Any]) -> Catalog:
    """Validate and migrate raw catalog data to the current schema.

    Accepts schema version 1 (upgrades to 2) and version 2. Missing
    ``schema_version`` is invalid. Unknown future versions fail closed.
    Legacy per-entry ``lifecycle`` keys are stripped.
    """
    if not isinstance(data, dict):
        raise ConfigurationError("Catalog root must be an object")
    if "schema_version" not in data:
        raise ConfigurationError("Catalog missing schema_version")
    version = data.get("schema_version")
    if version not in (1, 2):
        raise ConfigurationError(f"Unsupported catalog schema_version={version!r}; expected 1 or 2")

    payload = dict(data)
    history = list(payload.get("migration_history") or [])
    if not isinstance(history, list):
        history = []

    entries = payload.get("entries")
    if isinstance(entries, dict):
        for raw_entry in entries.values():
            if isinstance(raw_entry, dict):
                raw_entry.pop("lifecycle", None)

    if version == 1:
        # Upgrade resource clocks: index + each entry.page
        index = payload.get("index")
        if isinstance(index, dict):
            payload["index"] = _migrate_resource_state_v1_to_v2(index)
        if isinstance(entries, dict):
            migrated_entries: dict[str, Any] = {}
            for sid, raw_entry in entries.items():
                if not isinstance(raw_entry, dict):
                    migrated_entries[sid] = raw_entry
                    continue
                entry = dict(raw_entry)
                page = entry.get("page")
                if isinstance(page, dict):
                    entry["page"] = _migrate_resource_state_v1_to_v2(page)
                migrated_entries[sid] = entry
            payload["entries"] = migrated_entries
        payload["schema_version"] = CATALOG_SCHEMA_VERSION
        history.append(
            {
                "from": 1,
                "to": 2,
                "name": "resource_lifecycle_clocks",
            }
        )
        payload["migration_history"] = history

    try:
        return Catalog.model_validate(payload)
    except ValidationError as exc:
        raise ConfigurationError(f"Invalid catalog: {exc}") from exc


def load_catalog(path: Path) -> Catalog | None:
    """Load and validate a catalog from ``path``.

    Returns ``None`` when the file is missing. Corrupt or invalid content raises
    :class:`ConfigurationError` (never returns a partial catalog).
    """
    path = Path(path)
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"Corrupt catalog at {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigurationError(f"Corrupt catalog at {path}: root must be an object")
    try:
        return migrate_catalog(data)
    except FeedError as exc:
        raise ConfigurationError(f"Corrupt catalog at {path}: {exc}") from exc


def save_catalog(path: Path, catalog: Catalog) -> None:
    """Atomically write ``catalog`` to ``path``."""
    blob = catalog_to_json(catalog).encode("utf-8")
    atomic_write_bytes(Path(path), blob, mode=_FILE_MODE)


# ---------------------------------------------------------------------------
# Discovery reconcile
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChangeSet:
    """Stable-id deltas from one discovery reconcile pass."""

    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)


def reconcile_discovery(
    prior: Catalog | None,
    items: list[DiscoveryItem],
    *,
    now: datetime,
) -> tuple[Catalog, ChangeSet]:
    """Reconcile discovered index items against a prior durable catalog.

    Catalog membership mirrors the current index only: essays absent from
    ``items`` are hard-deleted (not soft-retained). Prior enrichment is reused
    when a rediscovered id still exists in ``prior.entries``.

    Parameters
    ----------
    prior:
        Previous catalog, or ``None`` for a cold bootstrap.
    items:
        Ordered discovery list (newest first). Catalog positions are assigned
        as ``0..n-1`` in this order.
    now:
        Aware UTC observation instant for first/last/observed timestamps.

    Returns
    -------
    tuple[Catalog, ChangeSet]
        Next catalog snapshot and the stable-id change classification.
    """
    observed_at = require_aware_utc(now)
    prior_entries = dict(prior.entries) if prior is not None else {}
    prior_order = set(prior.entry_order) if prior is not None else set()
    discovered_ids = {item.stable_id for item in items}

    added: list[str] = []
    updated: list[str] = []
    unchanged: list[str] = []
    next_entries: dict[str, CatalogEntry] = {}
    entry_order: list[str] = []

    for position, item in enumerate(items):
        stable_id = item.stable_id
        entry_order.append(stable_id)
        existing = prior_entries.get(stable_id)
        if existing is None:
            next_entries[stable_id] = CatalogEntry(
                stable_id=stable_id,
                url=item.url,
                title=item.title,
                position=position,
                first_seen_at=observed_at,
                last_seen_at=observed_at,
                observed_updated_at=observed_at,
            )
            added.append(stable_id)
            continue

        # Position/order is integrity metadata, not essay material (RES-H09).
        material_changed = existing.title != item.title or existing.url != item.url
        # model_copy preserves enrichment (summary*, page, published_*) and
        # first_seen_at unless explicitly overwritten.
        next_entries[stable_id] = existing.model_copy(
            update={
                "url": item.url,
                "title": item.title,
                "position": position,
                "last_seen_at": observed_at,
                "observed_updated_at": (
                    observed_at if material_changed else existing.observed_updated_at
                ),
            }
        )
        if material_changed:
            updated.append(stable_id)
        else:
            unchanged.append(stable_id)

    removed = sorted(prior_order - discovered_ids)

    if prior is None:
        catalog = Catalog(
            schema_version=_DEFAULT_SCHEMA_VERSION,
            material_config_fingerprint=_DEFAULT_FINGERPRINT,
            versions={},
            entry_order=entry_order,
            entries=next_entries,
        )
    else:
        catalog = Catalog(
            schema_version=prior.schema_version,
            material_config_fingerprint=prior.material_config_fingerprint,
            versions=dict(prior.versions),
            index=prior.index,
            entry_order=entry_order,
            entries=next_entries,
            last_generation_id=prior.last_generation_id,
            migration_history=list(prior.migration_history),
        )

    changeset = ChangeSet(
        added=added,
        removed=removed,
        updated=updated,
        unchanged=unchanged,
    )
    return catalog, changeset


# ---------------------------------------------------------------------------
# Refresh planner
# ---------------------------------------------------------------------------


class RefreshReason(StrEnum):
    """Why a catalog entry (or the index) is selected for refresh work."""

    NEW = "new"
    INDEX_CHANGED = "index_changed"
    STALE = "stale"
    RETRY = "retry"
    MISSING_METADATA = "missing_metadata"
    FORCE = "force"
    CANARY = "canary"
    NOT_DUE = "not_due"


@dataclass(frozen=True, slots=True)
class RefreshDecision:
    """Per-entry refresh choice for one catalog stable_id."""

    stable_id: str
    fetch_page: bool
    reasons: tuple[RefreshReason, ...]


@dataclass(frozen=True, slots=True)
class RefreshPlan:
    """Index + ordered entry decisions for one planner invocation."""

    fetch_index: bool
    decisions: list[RefreshDecision]


def plan_refresh(
    catalog: Catalog,
    *,
    force: bool = False,
    enrich: bool = True,
    stale_after_days: int = 30,
    now: datetime,
    canary_ids: frozenset[str] | None = None,
    max_page_fetches: int | None = None,
    page_fetch_cursor: int | None = None,
) -> RefreshPlan:
    """Build a deterministic refresh plan from catalog state and policy knobs.

    Rules:

    1. ``force=True`` → every entry is marked ``FORCE``; ``fetch_page`` is True
       for the first ``max_page_fetches`` ids in ``entry_order`` (or all when
       the budget is ``None``).
    2. ``NEW`` is the reconcile caller's job. For an existing catalog entry,
       ``MISSING_METADATA`` applies when ``enrich`` is True and the summary is
       ``None`` or empty.
    3. ``STALE`` when ``page.last_checked_at`` is ``None`` or older than
       ``stale_after_days``.
    4. ``CANARY`` when ``stable_id`` is in ``canary_ids``.
    5. Otherwise ``NOT_DUE`` with ``fetch_page=False``.
    6. ``fetch_index`` is True when forced or the index is unchecked/stale.
    7. Decisions are emitted in ``catalog.entry_order``.
    """
    current = require_aware_utc(now)
    canaries = canary_ids if canary_ids is not None else frozenset()

    fetch_index = force or _is_stale(
        _success_clock(catalog.index),
        now=current,
        stale_after_days=stale_after_days,
    )

    provisional: list[RefreshDecision] = []
    for stable_id in catalog.entry_order:
        entry = catalog.entries.get(stable_id)
        if entry is None:
            continue
        reasons = _entry_reasons(
            entry,
            force=force,
            enrich=enrich,
            stale_after_days=stale_after_days,
            now=current,
            canaries=canaries,
        )
        wants_fetch = RefreshReason.NOT_DUE not in reasons
        provisional.append(
            RefreshDecision(
                stable_id=stable_id,
                fetch_page=wants_fetch,
                reasons=reasons,
            )
        )

    cursor = 0
    if page_fetch_cursor is not None:
        cursor = max(0, page_fetch_cursor)
    else:
        raw_cursor = catalog.versions.get("page_fetch_cursor")
        if raw_cursor is not None:
            with contextlib.suppress(TypeError, ValueError):
                cursor = max(0, int(raw_cursor))
    decisions = _apply_page_fetch_budget(
        provisional, max_page_fetches=max_page_fetches, cursor=cursor
    )
    return RefreshPlan(fetch_index=fetch_index, decisions=decisions)


def _entry_reasons(
    entry: CatalogEntry,
    *,
    force: bool,
    enrich: bool,
    stale_after_days: int,
    now: datetime,
    canaries: frozenset[str],
) -> tuple[RefreshReason, ...]:
    if force:
        return (RefreshReason.FORCE,)

    # Page fetches only matter when enrichment (or force/canary) is active.
    # Index-only runs must not plan per-page work solely because page state is
    # unchecked — that reintroduced full rewrites every pass (F-001 adjacent).
    reasons: list[RefreshReason] = []
    if enrich and _is_stale(
        _success_clock(entry.page),
        now=now,
        stale_after_days=stale_after_days,
    ):
        reasons.append(RefreshReason.STALE)
    if enrich and _missing_summary(entry.summary) and _missing_summary_due(entry.page, now=now):
        reasons.append(RefreshReason.MISSING_METADATA)
    if entry.stable_id in canaries:
        reasons.append(RefreshReason.CANARY)
    if not reasons:
        return (RefreshReason.NOT_DUE,)
    return tuple(reasons)


def _missing_summary(summary: str | None) -> bool:
    return summary is None or not summary.strip()


def _success_clock(page: object) -> datetime | None:
    """Prefer last_success_at; fall back to legacy last_checked_at."""
    last_success = getattr(page, "last_success_at", None)
    if last_success is not None:
        return last_success  # type: ignore[no-any-return]
    return getattr(page, "last_checked_at", None)  # type: ignore[no-any-return]


def _missing_summary_due(page: object, *, now: datetime) -> bool:
    """Whether a missing summary should re-queue (respect failure backoff)."""
    next_retry = getattr(page, "next_retry_at", None)
    if next_retry is not None:
        return now >= next_retry  # type: ignore[no-any-return]
    return True


def _is_stale(
    last_success_at: datetime | None,
    *,
    now: datetime,
    stale_after_days: int,
) -> bool:
    """True when never successfully validated, future-dated, or past TTL."""
    if last_success_at is None:
        return True
    # Future clocks are treated as stale (fail closed) so wall-clock catch-up
    # cannot hide resources from refresh forever.
    if last_success_at > now:
        return True
    return (now - last_success_at) >= timedelta(days=stale_after_days)


def failure_backoff_delta(*, failure_count: int) -> timedelta:
    """Bounded exponential backoff after consecutive failures (hours → days)."""
    # 1h, 2h, 4h, … capped at _MAX_FAILURE_BACKOFF_DAYS.
    hours = min(24 * _MAX_FAILURE_BACKOFF_DAYS, 2 ** max(0, failure_count - 1))
    return timedelta(hours=hours)


def _apply_page_fetch_budget(
    decisions: list[RefreshDecision],
    *,
    max_page_fetches: int | None,
    cursor: int = 0,
) -> list[RefreshDecision]:
    """Cap how many due entries get ``fetch_page=True`` with a rotating cursor.

    Starts at ``cursor % len(decisions)`` so repeated runs do not always starve
    the tail of ``entry_order`` (M-04).
    """
    if max_page_fetches is None:
        return decisions

    budget = max(0, max_page_fetches)
    if not decisions or budget == 0:
        return [
            RefreshDecision(
                stable_id=d.stable_id,
                fetch_page=False,
                reasons=d.reasons,
            )
            if d.fetch_page
            else d
            for d in decisions
        ]

    n = len(decisions)
    start = cursor % n
    used = 0
    allowed: set[str] = set()
    for offset in range(n):
        decision = decisions[(start + offset) % n]
        if decision.fetch_page and used < budget:
            allowed.add(decision.stable_id)
            used += 1
            # else over budget — clear fetch_page below

    out: list[RefreshDecision] = []
    for decision in decisions:
        if decision.fetch_page and decision.stable_id not in allowed:
            out.append(
                RefreshDecision(
                    stable_id=decision.stable_id,
                    fetch_page=False,
                    reasons=decision.reasons,
                )
            )
        else:
            out.append(decision)
    return out
