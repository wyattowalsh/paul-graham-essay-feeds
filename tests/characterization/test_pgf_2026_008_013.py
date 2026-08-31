"""PGF-2026-008 fair cursor persistence; PGF-2026-013 absence hysteresis."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from paul_graham_essay_feeds.catalog import (
    PAGE_FETCH_CURSOR_KEY,
    catalog_to_json,
    catalog_with_page_fetch_cursor,
    page_fetch_cursor_after_attempts,
    plan_refresh,
    reconcile_discovery,
)
from paul_graham_essay_feeds.discover import (
    ExtractionReport,
    ExtractionStrategy,
    evaluate_discovery_anomaly,
)
from paul_graham_essay_feeds.models import (
    ABSENCE_QUARANTINE_MIN_REMOVED,
    Catalog,
    CatalogEntry,
    DiscoveryItem,
    ResourceState,
)

T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
T1 = datetime(2024, 6, 15, 9, 30, 0, tzinfo=UTC)
NOW = datetime(2026, 7, 25, 12, 0, 0, tzinfo=UTC)

pytestmark = pytest.mark.characterization


def _item(slug: str) -> DiscoveryItem:
    url = f"https://paulgraham.com/{slug}.html"
    return DiscoveryItem(
        position=1,
        title=slug.upper(),
        url=url,
        stable_id=url,
        is_permalink=True,
    )


def _report() -> ExtractionReport:
    return ExtractionReport(
        strategy=ExtractionStrategy.MARKER,
        fallback_used=False,
        marked_count=20,
        drift_score=0.0,
    )


def test_pgf_2026_013_one_run_omission_does_not_publish_deletion() -> None:
    items = [_item(f"e{i}") for i in range(6)]
    prior, _ = reconcile_discovery(None, items, now=T0)
    held_cat, changes = reconcile_discovery(prior, items[:5], now=T1)
    gone = items[5].stable_id
    assert changes.removed == []
    assert changes.held == [gone]
    assert gone in held_cat.entry_order
    assert held_cat.entries[gone].consecutive_absences == 1
    dumped = catalog_to_json(held_cat)
    assert "tombstone" not in dumped
    assert "lifecycle" not in dumped
    assert '"consecutive_absences": 1' in dumped

    deleted, second = reconcile_discovery(held_cat, items[:5], now=T1)
    assert second.removed == [gone]
    assert gone not in deleted.entries

    _five_held, five_cs = reconcile_discovery(prior, items[:1], now=T1)
    assert five_cs.removed == []
    assert len(five_cs.held) == 5


def test_pgf_2026_013_quarantine_floor_unchanged() -> None:
    prior = {f"https://paulgraham.com/e{i}.html" for i in range(20)}
    keep = set(list(prior)[4:])
    assert evaluate_discovery_anomaly(prior, keep, report=_report()) is None
    drop_five = set(list(prior)[5:])
    reason = evaluate_discovery_anomaly(prior, drop_five, report=_report())
    assert reason is not None
    assert ABSENCE_QUARANTINE_MIN_REMOVED == 5


def test_pgf_2026_008_cursor_is_last_selected_plus_one() -> None:
    entries = []
    for i, slug in enumerate("abcd"):
        sid = f"https://paulgraham.com/{slug}.html"
        last_success = NOW if slug == "a" else None
        entries.append(
            CatalogEntry(
                stable_id=sid,
                url=sid,
                title=slug.upper(),
                position=i,
                summary="A short summary.",
                page=ResourceState(last_success_at=last_success),
            )
        )
    catalog = Catalog(
        schema_version=3,
        material_config_fingerprint="fp",
        index=ResourceState(last_success_at=NOW),
        entry_order=[e.stable_id for e in entries],
        entries={e.stable_id: e.model_copy(update={"position": i}) for i, e in enumerate(entries)},
    )
    plan = plan_refresh(
        catalog,
        now=NOW,
        stale_after_days=30,
        max_page_fetches=2,
        page_fetch_cursor=0,
    )
    assert page_fetch_cursor_after_attempts(plan.decisions, cursor=0) == 3
    stamped = catalog_with_page_fetch_cursor(catalog, plan.decisions, cursor=0)
    assert stamped.versions[PAGE_FETCH_CURSOR_KEY] == "3"
