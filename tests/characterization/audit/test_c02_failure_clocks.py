"""C-02 / M-02 / M-20: failed refreshes must not mint a success TTL."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from paul_graham_essay_feeds.catalog import (
    _is_stale,
    failure_backoff_delta,
    migrate_catalog,
    plan_refresh,
)
from paul_graham_essay_feeds.models import Catalog, CatalogEntry, ResourceState

T0 = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)


def test_c02_is_stale_uses_success_clock_not_attempt() -> None:
    # Recent attempt but no success → still stale.
    assert _is_stale(None, now=T0, stale_after_days=30) is True
    # Successful within TTL → not stale.
    assert _is_stale(T0 - timedelta(days=1), now=T0, stale_after_days=30) is False
    # Future success clock → stale (fail closed).
    assert _is_stale(T0 + timedelta(days=1), now=T0, stale_after_days=30) is True


def test_c02_missing_summary_respects_next_retry() -> None:
    entry = CatalogEntry(
        stable_id="https://paulgraham.com/a.html",
        url="https://paulgraham.com/a.html",
        title="A",
        position=0,
        summary=None,
        page=ResourceState(
            last_success_at=T0 - timedelta(days=1),
            failure_count=2,
            next_retry_at=T0 + timedelta(hours=2),
        ),
    )
    catalog = Catalog(
        schema_version=2,
        material_config_fingerprint="fp",
        entry_order=[entry.stable_id],
        entries={entry.stable_id: entry},
    )
    plan = plan_refresh(catalog, enrich=True, stale_after_days=30, now=T0)
    assert plan.decisions[0].fetch_page is False

    plan_due = plan_refresh(catalog, enrich=True, stale_after_days=30, now=T0 + timedelta(hours=3))
    assert plan_due.decisions[0].fetch_page is True


def test_c02_migrate_v1_to_v2_maps_success_clock() -> None:
    cat = migrate_catalog(
        {
            "schema_version": 1,
            "material_config_fingerprint": "fp",
            "entry_order": ["https://paulgraham.com/a.html"],
            "entries": {
                "https://paulgraham.com/a.html": {
                    "stable_id": "https://paulgraham.com/a.html",
                    "url": "https://paulgraham.com/a.html",
                    "title": "A",
                    "position": 0,
                    "page": {
                        "last_checked_at": "2024-01-01T00:00:00Z",
                        "status_code": 200,
                    },
                }
            },
        }
    )
    assert cat.schema_version == 2
    page = cat.entries["https://paulgraham.com/a.html"].page
    assert page.last_success_at is not None
    assert page.last_checked_at is not None
    assert page.failure_count == 0
    assert cat.migration_history[-1]["name"] == "resource_lifecycle_clocks"


def test_c02_failure_backoff_bounded() -> None:
    assert failure_backoff_delta(failure_count=1) == timedelta(hours=1)
    assert failure_backoff_delta(failure_count=20) <= timedelta(days=7)


def test_c02_migrate_v1_idempotent_preserves_success_mapping() -> None:
    raw = {
        "schema_version": 1,
        "material_config_fingerprint": "fp",
        "entry_order": ["https://paulgraham.com/a.html"],
        "entries": {
            "https://paulgraham.com/a.html": {
                "stable_id": "https://paulgraham.com/a.html",
                "url": "https://paulgraham.com/a.html",
                "title": "A",
                "position": 0,
                "page": {
                    "last_checked_at": "2024-01-01T00:00:00Z",
                    "status_code": 200,
                },
            }
        },
    }
    first = migrate_catalog(raw)
    second = migrate_catalog(first.model_dump(mode="json"))
    assert first.schema_version == 2
    assert second.schema_version == 2
    assert second.migration_history == first.migration_history
    page = second.entries["https://paulgraham.com/a.html"].page
    assert page.last_success_at is not None
    assert page.last_attempted_at is not None


def test_c02_committed_catalog_is_schema_version_2() -> None:
    from pathlib import Path

    from paul_graham_essay_feeds.catalog import load_catalog

    root = Path(__file__).resolve().parents[3]
    catalog = load_catalog(root / "catalog.json")
    assert catalog is not None
    assert catalog.schema_version == 2
