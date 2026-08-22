"""Unit tests for catalog behavior (I/O, bootstrap, reconcile, refresh, atomic writes)."""

from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from paul_graham_essay_feeds.catalog import (
    CATALOG_SCHEMA_VERSION,
    DEFAULT_CATALOG_REL,
    ChangeSet,
    RefreshReason,
    atomic_write_bytes,
    atomic_write_text,
    bootstrap_catalog_from_feeds,
    catalog_to_json,
    default_catalog_path,
    empty_catalog,
    load_catalog,
    migrate_catalog,
    plan_refresh,
    reconcile_discovery,
    save_catalog,
)
from paul_graham_essay_feeds.models import (
    Catalog,
    CatalogEntry,
    DiscoveryItem,
    FeedEntrySnapshot,
    FeedError,
    FeedSnapshot,
    ResourceState,
)

T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
T1 = datetime(2024, 6, 15, 9, 30, 0, tzinfo=UTC)
T2 = datetime(2024, 7, 1, 0, 0, 0, tzinfo=UTC)
NOW = datetime(2026, 7, 25, 12, 0, 0, tzinfo=UTC)
STALE_AFTER = 30


# ---------------------------------------------------------------------------
# Schema (from test_catalog_models)
# ---------------------------------------------------------------------------


def test_catalog_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        Catalog.model_validate(
            {
                "schema_version": 1,
                "material_config_fingerprint": "x",
                "unexpected": True,
            }
        )


def test_catalog_entry_utc() -> None:
    entry = CatalogEntry(
        stable_id="https://paulgraham.com/a.html",
        url="https://paulgraham.com/a.html",
        title="A",
        position=0,
        first_seen_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    assert entry.first_seen_at is not None
    assert entry.first_seen_at.tzinfo is not None


def test_naive_datetime_rejected_on_entry() -> None:
    with pytest.raises(FeedError, match="Naive"):
        CatalogEntry(
            stable_id="id",
            url="https://paulgraham.com/a.html",
            title="A",
            position=0,
            first_seen_at=datetime(2024, 1, 1),
        )


def test_feed_snapshot_requires_items() -> None:
    with pytest.raises(ValidationError):
        FeedSnapshot(
            logical_updated_at=datetime(2024, 1, 1, tzinfo=UTC),
            generator="pg-essay-feeds/0.1.0",
            items=[],
        )


def test_feed_snapshot_roundtrip() -> None:
    snap = FeedSnapshot(
        logical_updated_at=datetime(2024, 6, 1, tzinfo=UTC),
        generator="pg-essay-feeds/0.1.0",
        items=[
            FeedEntrySnapshot(
                id="https://paulgraham.com/a.html",
                url="https://paulgraham.com/a.html",
                title="A",
                summary="A short summary for the feed item.",
                observed_updated_at=datetime(2024, 6, 1, tzinfo=UTC),
            )
        ],
    )
    restored = FeedSnapshot.model_validate(snap.model_dump(mode="json"))
    assert restored.items[0].title == "A"


# ---------------------------------------------------------------------------
# Catalog I/O (from test_catalog_store)
# ---------------------------------------------------------------------------


def _minimal_catalog(*, fingerprint: str = "fp-test") -> Catalog:
    return empty_catalog(material_config_fingerprint=fingerprint, versions={"gen": "1"})


def test_default_catalog_path() -> None:
    root = Path("/tmp/repo")
    assert default_catalog_path(root) == root / "catalog.json"
    assert Path("catalog.json") == DEFAULT_CATALOG_REL


def test_empty_catalog_schema_current() -> None:
    cat = empty_catalog(material_config_fingerprint="abc")
    assert cat.schema_version == CATALOG_SCHEMA_VERSION == 2
    assert cat.material_config_fingerprint == "abc"
    assert cat.versions == {}
    assert cat.entries == {}
    assert cat.entry_order == []
    assert cat.last_generation_id is None


def test_empty_catalog_with_versions() -> None:
    cat = empty_catalog(
        material_config_fingerprint="fp",
        versions={"generator": "0.1.0", "decoder": "1"},
    )
    assert cat.versions == {"generator": "0.1.0", "decoder": "1"}


def test_load_missing_returns_none(tmp_path: Path) -> None:
    assert load_catalog(tmp_path / "catalog.json") is None


def test_save_load_roundtrip(tmp_path: Path) -> None:
    path = default_catalog_path(tmp_path)
    entry = CatalogEntry(
        stable_id="https://paulgraham.com/a.html",
        url="https://paulgraham.com/a.html",
        title="A",
        position=0,
    )
    original = Catalog(
        schema_version=1,
        material_config_fingerprint="fp-round",
        versions={"g": "1"},
        entry_order=[entry.stable_id],
        entries={entry.stable_id: entry},
    )
    save_catalog(path, original)
    loaded = load_catalog(path)
    assert loaded is not None
    # Load migrates schema v1 → v2 (resource lifecycle clocks).
    assert loaded.schema_version == 2
    assert loaded.entries[entry.stable_id].title == "A"
    assert loaded.material_config_fingerprint == "fp-round"
    assert loaded.migration_history
    assert loaded.migration_history[-1]["to"] == 2


def test_deterministic_json_sorted_keys_and_newline(tmp_path: Path) -> None:
    cat = empty_catalog(
        material_config_fingerprint="z-fp",
        versions={"b": "2", "a": "1"},
    )
    text = catalog_to_json(cat)
    assert text.endswith("\n")
    assert not text.endswith("\n\n")
    again = catalog_to_json(Catalog.model_validate(json.loads(text)))
    assert again == text
    payload = json.loads(text)
    assert list(payload.keys()) == sorted(payload.keys())
    assert list(payload["versions"].keys()) == ["a", "b"]

    path = default_catalog_path(tmp_path)
    save_catalog(path, cat)
    on_disk = path.read_text(encoding="utf-8")
    assert on_disk == text
    save_catalog(path, cat)
    assert path.read_text(encoding="utf-8") == on_disk


def test_corrupt_json_raises(tmp_path: Path) -> None:
    path = default_catalog_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(FeedError, match="Corrupt catalog"):
        load_catalog(path)


def test_corrupt_non_object_raises(tmp_path: Path) -> None:
    path = default_catalog_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[1, 2, 3]\n", encoding="utf-8")
    with pytest.raises(FeedError, match="root must be an object"):
        load_catalog(path)


def test_invalid_schema_raises(tmp_path: Path) -> None:
    path = default_catalog_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"schema_version": 1, "material_config_fingerprint": ""}),
        encoding="utf-8",
    )
    with pytest.raises(FeedError, match="Corrupt catalog"):
        load_catalog(path)


def test_migrate_requires_schema_version() -> None:
    with pytest.raises(FeedError, match="schema_version"):
        migrate_catalog({"material_config_fingerprint": "x"})


def test_migrate_valid_current_schema() -> None:
    cat = migrate_catalog(
        {
            "schema_version": 1,
            "material_config_fingerprint": "fp",
        }
    )
    assert cat.schema_version == 2
    assert cat.migration_history
    assert cat.migration_history[-1]["from"] == 1
    assert cat.migration_history[-1]["to"] == 2
    assert cat.material_config_fingerprint == "fp"


def test_migrate_rejects_unknown_schema_version() -> None:
    with pytest.raises(FeedError, match="Unsupported catalog schema_version"):
        migrate_catalog(
            {
                "schema_version": 99,
                "material_config_fingerprint": "fp",
            }
        )


def test_migrate_rejects_extra_fields() -> None:
    with pytest.raises(FeedError, match="Invalid catalog"):
        migrate_catalog(
            {
                "schema_version": 1,
                "material_config_fingerprint": "fp",
                "unexpected": True,
            }
        )


def test_migrate_strips_legacy_lifecycle_keys() -> None:
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
                    "lifecycle": "active",
                }
            },
        }
    )
    assert "lifecycle" not in cat.entries["https://paulgraham.com/a.html"].model_dump()
    assert cat.entry_order == ["https://paulgraham.com/a.html"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits only")
def test_save_file_mode_0644(tmp_path: Path) -> None:
    path = default_catalog_path(tmp_path)
    save_catalog(path, _minimal_catalog())
    mode = path.stat().st_mode
    assert mode & stat.S_IRUSR
    assert mode & stat.S_IRGRP, f"not group-readable: {oct(mode)}"
    assert mode & stat.S_IROTH, f"not other-readable: {oct(mode)}"
    assert not (mode & stat.S_IXUSR)


def test_save_cleans_tmp_on_replace_failure(tmp_path: Path) -> None:
    path = default_catalog_path(tmp_path)
    prior = _minimal_catalog(fingerprint="prior")
    save_catalog(path, prior)
    prior_bytes = path.read_bytes()

    with (
        patch(
            "paul_graham_essay_feeds.catalog.os.replace",
            side_effect=OSError("disk full"),
        ),
        pytest.raises(OSError, match="disk full"),
    ):
        save_catalog(path, _minimal_catalog(fingerprint="new"))

    assert path.read_bytes() == prior_bytes
    assert list(path.parent.glob(f".{path.name}.*")) == []


def test_load_missing_schema_version_on_disk(tmp_path: Path) -> None:
    path = default_catalog_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"material_config_fingerprint": "legacy"}),
        encoding="utf-8",
    )
    with pytest.raises(FeedError, match="schema_version"):
        load_catalog(path)


# ---------------------------------------------------------------------------
# Bootstrap (from test_bootstrap)
# ---------------------------------------------------------------------------


def _write_feed(root: Path, items: list[object]) -> None:
    feeds = root / "feeds"
    feeds.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": "https://jsonfeed.org/version/1.1",
        "title": "Paul Graham: Essays",
        "items": items,
    }
    (feeds / "feed.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def test_bootstrap_from_synthetic_feed_json(tmp_path: Path) -> None:
    now = datetime(2026, 7, 25, 12, 0, 0, tzinfo=UTC)
    _write_feed(
        tmp_path,
        [
            {
                "id": "https://paulgraham.com/a.html",
                "url": "https://paulgraham.com/a.html",
                "title": "Essay A",
                "summary": "Short summary for essay A content.",
            },
            {
                "id": "https://paulgraham.com/b.html",
                "url": "https://paulgraham.com/b.html",
                "title": "Essay B",
                "summary": "Short summary for essay B content.",
            },
        ],
    )

    catalog = bootstrap_catalog_from_feeds(tmp_path, now=now)

    assert catalog.schema_version == 2
    assert catalog.material_config_fingerprint == "bootstrap"
    assert catalog.entry_order == [
        "https://paulgraham.com/a.html",
        "https://paulgraham.com/b.html",
    ]
    assert set(catalog.entries) == set(catalog.entry_order)

    first = catalog.entries["https://paulgraham.com/a.html"]
    assert first.position == 0
    assert first.title == "Essay A"
    assert first.url == "https://paulgraham.com/a.html"
    assert first.summary == "Short summary for essay A content."
    assert first.prior_good_summary == first.summary
    assert first.first_seen_at == now
    assert first.last_seen_at == now
    assert first.observed_updated_at == now
    assert first.observed_updated_at is not None
    assert first.observed_updated_at.year != 1970

    second = catalog.entries["https://paulgraham.com/b.html"]
    assert second.position == 1
    assert second.summary == "Short summary for essay B content."
    assert second.prior_good_summary == second.summary


def test_missing_feeds_returns_empty_catalog(tmp_path: Path) -> None:
    now = datetime(2026, 7, 25, 12, 0, 0, tzinfo=UTC)
    catalog = bootstrap_catalog_from_feeds(tmp_path, now=now)

    assert catalog.schema_version == 2
    assert catalog.material_config_fingerprint == "bootstrap"
    assert catalog.entry_order == []
    assert catalog.entries == {}


def test_fffd_summary_still_loads(tmp_path: Path) -> None:
    now = datetime(2026, 7, 25, 12, 0, 0, tzinfo=UTC)
    dirty = "Broken summary with \ufffd replacement char still loads."
    _write_feed(
        tmp_path,
        [
            {
                "id": "https://paulgraham.com/c.html",
                "url": "https://paulgraham.com/c.html",
                "title": "Essay C",
                "summary": dirty,
            },
        ],
    )

    catalog = bootstrap_catalog_from_feeds(tmp_path, now=now)

    assert len(catalog.entries) == 1
    entry = catalog.entries["https://paulgraham.com/c.html"]
    assert entry.summary == dirty
    assert entry.prior_good_summary == dirty
    assert "\ufffd" in (entry.summary or "")


def test_bootstrap_fail_closed_on_corrupt_json(tmp_path: Path) -> None:
    feeds = tmp_path / "feeds"
    feeds.mkdir(parents=True)
    (feeds / "feed.json").write_text("{not-json", encoding="utf-8")
    now = datetime(2026, 7, 25, 12, 0, 0, tzinfo=UTC)
    with pytest.raises(FeedError, match=r"Unable to read bootstrap feed\.json"):
        bootstrap_catalog_from_feeds(tmp_path, now=now)


def test_bootstrap_fail_closed_on_non_object_root(tmp_path: Path) -> None:
    feeds = tmp_path / "feeds"
    feeds.mkdir(parents=True)
    (feeds / "feed.json").write_text("[]\n", encoding="utf-8")
    now = datetime(2026, 7, 25, 12, 0, 0, tzinfo=UTC)
    with pytest.raises(FeedError, match=r"feed\.json root must be an object"):
        bootstrap_catalog_from_feeds(tmp_path, now=now)


def test_bootstrap_fail_closed_on_items_not_array(tmp_path: Path) -> None:
    feeds = tmp_path / "feeds"
    feeds.mkdir(parents=True)
    (feeds / "feed.json").write_text(
        json.dumps({"version": "https://jsonfeed.org/version/1.1", "items": {}}) + "\n",
        encoding="utf-8",
    )
    now = datetime(2026, 7, 25, 12, 0, 0, tzinfo=UTC)
    with pytest.raises(FeedError, match=r"feed\.json missing items array"):
        bootstrap_catalog_from_feeds(tmp_path, now=now)


def test_bootstrap_skips_incomplete_items(tmp_path: Path) -> None:
    now = datetime(2026, 7, 25, 12, 0, 0, tzinfo=UTC)
    _write_feed(
        tmp_path,
        [
            {"id": "https://paulgraham.com/a.html", "title": "No URL"},
            {
                "id": "https://paulgraham.com/b.html",
                "url": "https://paulgraham.com/b.html",
                "title": "Essay B",
                "summary": "Valid summary for essay B content here.",
            },
            "not-an-object",
        ],
    )
    catalog = bootstrap_catalog_from_feeds(tmp_path, now=now)
    assert catalog.entry_order == ["https://paulgraham.com/b.html"]
    assert "https://paulgraham.com/a.html" not in catalog.entries


# ---------------------------------------------------------------------------
# Reconcile (from test_reconcile)
# ---------------------------------------------------------------------------


def _item(
    *,
    slug: str,
    title: str | None = None,
    position: int = 1,
) -> DiscoveryItem:
    url = f"https://paulgraham.com/{slug}.html"
    return DiscoveryItem(
        position=position,
        title=title if title is not None else slug.upper(),
        url=url,
        stable_id=url,
        is_permalink=True,
    )


def _entry(
    item: DiscoveryItem,
    *,
    position: int,
    first_seen_at: datetime = T0,
    last_seen_at: datetime = T0,
    observed_updated_at: datetime = T0,
    summary: str | None = None,
    summary_source: str | None = None,
    summary_quality: float | None = None,
    prior_good_summary: str | None = None,
    published_hint: str | None = None,
    published_at: datetime | None = None,
    page: ResourceState | None = None,
) -> CatalogEntry:
    return CatalogEntry(
        stable_id=item.stable_id,
        url=item.url,
        title=item.title,
        position=position,
        first_seen_at=first_seen_at,
        last_seen_at=last_seen_at,
        observed_updated_at=observed_updated_at,
        summary=summary,
        summary_source=summary_source,
        summary_quality=summary_quality,
        prior_good_summary=prior_good_summary,
        published_hint=published_hint,
        published_at=published_at,
        page=page if page is not None else ResourceState(),
    )


def test_bootstrap_from_empty_prior() -> None:
    essays = [_item(slug="a", position=1), _item(slug="b", position=2)]
    catalog, changes = reconcile_discovery(None, essays, now=T1)

    assert catalog.schema_version == 2
    assert catalog.material_config_fingerprint == "default"
    assert catalog.versions == {}
    assert catalog.entry_order == [
        "https://paulgraham.com/a.html",
        "https://paulgraham.com/b.html",
    ]
    assert set(catalog.entries) == set(catalog.entry_order)
    assert changes.added == catalog.entry_order
    assert changes.updated == []
    assert changes.unchanged == []
    assert changes.removed == []

    for idx, sid in enumerate(catalog.entry_order):
        entry = catalog.entries[sid]
        assert entry.position == idx
        assert entry.first_seen_at == T1
        assert entry.last_seen_at == T1
        assert entry.observed_updated_at == T1


def test_unchanged_existing_updates_last_seen_only() -> None:
    a = _item(slug="a", position=1)
    prior = Catalog(
        schema_version=1,
        material_config_fingerprint="cfg-abc",
        versions={"generator": "1.0"},
        entry_order=[a.stable_id],
        entries={
            a.stable_id: _entry(
                a,
                position=0,
                summary="kept summary",
                summary_source="meta",
                summary_quality=0.9,
                prior_good_summary="prior good",
                published_hint="June 2024",
                published_at=T0,
                page=ResourceState(etag='"e1"', raw_sha256="a" * 64),
            )
        },
        last_generation_id="gen-1",
        migration_history=[{"from": 1, "to": 1}],
    )

    catalog, changes = reconcile_discovery(prior, [a], now=T1)
    entry = catalog.entries[a.stable_id]

    assert changes.unchanged == [a.stable_id]
    assert changes.added == []
    assert changes.updated == []
    assert entry.last_seen_at == T1
    assert entry.first_seen_at == T0
    assert entry.observed_updated_at == T0
    assert entry.summary == "kept summary"
    assert entry.summary_source == "meta"
    assert entry.summary_quality == 0.9
    assert entry.prior_good_summary == "prior good"
    assert entry.published_hint == "June 2024"
    assert entry.published_at == T0
    assert entry.page.etag == '"e1"'
    assert entry.page.raw_sha256 == "a" * 64
    # Reconcile preserves prior schema_version until load/migrate.
    assert catalog.schema_version == 1
    assert catalog.material_config_fingerprint == "cfg-abc"
    assert catalog.versions == {"generator": "1.0"}
    assert catalog.last_generation_id == "gen-1"
    assert catalog.migration_history == [{"from": 1, "to": 1}]


def test_material_title_url_position_changes_mark_updated() -> None:
    a = _item(slug="a", title="Old", position=1)
    b = _item(slug="b", title="B", position=2)
    prior = Catalog(
        schema_version=1,
        material_config_fingerprint="default",
        entry_order=[a.stable_id, b.stable_id],
        entries={
            a.stable_id: _entry(a, position=0),
            b.stable_id: _entry(b, position=1),
        },
    )
    a_new = _item(slug="a", title="New Title", position=2)
    b_new = DiscoveryItem(
        position=1,
        title="B",
        url="https://paulgraham.com/b-renamed.html",
        stable_id=b.stable_id,
        is_permalink=True,
    )
    essays = [b_new, a_new]

    catalog, changes = reconcile_discovery(prior, essays, now=T1)

    # Title + URL are material; both entries update material clocks.
    assert set(changes.updated) == {a.stable_id, b.stable_id}
    assert changes.unchanged == []
    assert catalog.entries[a.stable_id].title == "New Title"
    assert catalog.entries[a.stable_id].position == 1
    assert catalog.entries[a.stable_id].observed_updated_at == T1
    assert catalog.entries[b.stable_id].url == "https://paulgraham.com/b-renamed.html"
    assert catalog.entries[b.stable_id].position == 0
    assert catalog.entries[b.stable_id].observed_updated_at == T1
    assert catalog.entry_order == [b.stable_id, a.stable_id]


def test_position_only_reorder_is_not_material() -> None:
    """RES-H09: position/order changes alone must not bump observed_updated_at."""
    a = _item(slug="a", title="A", position=1)
    b = _item(slug="b", title="B", position=2)
    prior = Catalog(
        schema_version=1,
        material_config_fingerprint="default",
        entry_order=[a.stable_id, b.stable_id],
        entries={
            a.stable_id: _entry(a, position=0),
            b.stable_id: _entry(b, position=1),
        },
    )
    # Same titles/urls; only list order swaps.
    essays = [
        _item(slug="b", title="B", position=1),
        _item(slug="a", title="A", position=2),
    ]
    catalog, changes = reconcile_discovery(prior, essays, now=T1)

    assert changes.updated == []
    assert set(changes.unchanged) == {a.stable_id, b.stable_id}
    assert catalog.entries[a.stable_id].position == 1
    assert catalog.entries[b.stable_id].position == 0
    assert catalog.entries[a.stable_id].observed_updated_at == T0
    assert catalog.entries[b.stable_id].observed_updated_at == T0
    assert catalog.entry_order == [b.stable_id, a.stable_id]


def test_new_id_is_added() -> None:
    a = _item(slug="a", position=1)
    prior = Catalog(
        schema_version=1,
        material_config_fingerprint="default",
        entry_order=[a.stable_id],
        entries={a.stable_id: _entry(a, position=0)},
    )
    c = _item(slug="c", position=1)
    essays = [c, a]

    catalog, changes = reconcile_discovery(prior, essays, now=T1)

    assert changes.added == [c.stable_id]
    # Position-only shift for `a` is not material (RES-H09).
    assert changes.updated == []
    assert changes.unchanged == [a.stable_id]
    assert catalog.entries[a.stable_id].position == 1
    assert catalog.entries[c.stable_id].first_seen_at == T1
    assert catalog.entry_order == [c.stable_id, a.stable_id]


def test_missing_index_essay_hard_deleted() -> None:
    a = _item(slug="a", position=1)
    b = _item(slug="b", position=2)
    prior = Catalog(
        schema_version=1,
        material_config_fingerprint="default",
        entry_order=[a.stable_id, b.stable_id],
        entries={
            a.stable_id: _entry(a, position=0),
            b.stable_id: _entry(b, position=1, summary="keep me"),
        },
    )

    catalog, changes = reconcile_discovery(prior, [a], now=T1)

    assert changes.removed == [b.stable_id]
    assert b.stable_id not in catalog.entries
    assert catalog.entry_order == [a.stable_id]
    assert a.stable_id in catalog.entries


def test_orphan_prior_entry_not_in_order_is_dropped() -> None:
    a = _item(slug="a", position=1)
    gone = _item(slug="gone", position=2)
    # Intentionally invalid intermediate prior (orphan entry) via model_construct.
    prior = Catalog.model_construct(
        schema_version=1,
        material_config_fingerprint="default",
        versions={},
        index=ResourceState(),
        entry_order=[a.stable_id],
        entries={
            a.stable_id: _entry(a, position=0),
            gone.stable_id: _entry(gone, position=1),
        },
        last_generation_id=None,
        migration_history=[],
    )

    catalog, changes = reconcile_discovery(prior, [a], now=T1)

    assert gone.stable_id not in changes.removed
    assert gone.stable_id not in catalog.entries
    assert catalog.entry_order == [a.stable_id]


def test_enrichment_reused_when_still_on_index() -> None:
    a = _item(slug="a", position=1)
    prior = Catalog(
        schema_version=1,
        material_config_fingerprint="default",
        entry_order=[a.stable_id],
        entries={a.stable_id: _entry(a, position=0, summary="old summary")},
    )

    catalog, changes = reconcile_discovery(prior, [a], now=T1)
    entry = catalog.entries[a.stable_id]

    assert entry.last_seen_at == T1
    assert entry.summary == "old summary"
    assert changes.unchanged == [a.stable_id]
    assert catalog.entry_order == [a.stable_id]


def test_rediscovery_after_hard_delete_is_added() -> None:
    """Essay removed from the catalog, then rediscovered → classified as added."""
    a = _item(slug="a", position=1)
    b = _item(slug="b", position=2)
    prior = Catalog(
        schema_version=1,
        material_config_fingerprint="default",
        entry_order=[a.stable_id, b.stable_id],
        entries={
            a.stable_id: _entry(a, position=0),
            b.stable_id: _entry(b, position=1, summary="old"),
        },
    )
    after_delete, deleted = reconcile_discovery(prior, [a], now=T1)
    assert b.stable_id in deleted.removed
    assert b.stable_id not in after_delete.entries

    catalog, changes = reconcile_discovery(after_delete, [a, b], now=T2)
    assert changes.added == [b.stable_id]
    assert changes.removed == []
    assert catalog.entries[b.stable_id].first_seen_at == T2
    assert catalog.entries[b.stable_id].summary is None


def test_reconcile_naive_now_rejected() -> None:
    with pytest.raises(FeedError, match="Naive"):
        reconcile_discovery(None, [_item(slug="a")], now=datetime(2024, 1, 1))


def test_changeset_is_frozen() -> None:
    cs = ChangeSet(added=["x"])
    assert cs.added == ["x"]
    params = getattr(ChangeSet, "__dataclass_params__", None)
    assert params is not None
    assert params.frozen is True


def test_position_is_zero_based_catalog_order() -> None:
    """Essay.position is 1-based; catalog positions are 0..n-1 from list order."""
    essays = [
        _item(slug="newest", position=99),
        _item(slug="mid", position=50),
        _item(slug="oldest", position=1),
    ]
    catalog, _ = reconcile_discovery(None, essays, now=T2)
    assert [catalog.entries[sid].position for sid in catalog.entry_order] == [0, 1, 2]


# ---------------------------------------------------------------------------
# Refresh planner (from test_refresh)
# ---------------------------------------------------------------------------


def _refresh_entry(
    stable_id: str,
    *,
    position: int = 0,
    summary: str | None = "A short summary.",
    last_checked_at: datetime | None = None,
    last_success_at: datetime | None = None,
    title: str | None = None,
) -> CatalogEntry:
    success = last_success_at if last_success_at is not None else last_checked_at
    return CatalogEntry(
        stable_id=stable_id,
        url=stable_id,
        title=title or stable_id.rsplit("/", 1)[-1],
        position=position,
        summary=summary,
        page=ResourceState(
            last_checked_at=last_checked_at,
            last_attempted_at=last_checked_at,
            last_success_at=success,
        ),
    )


def _refresh_catalog(
    entries: list[CatalogEntry],
    *,
    index_last_checked_at: datetime | None = None,
    index_last_success_at: datetime | None = None,
) -> Catalog:
    order = [e.stable_id for e in entries]
    # Ensure unique positions aligned with order for relational invariants.
    normalized = {e.stable_id: e.model_copy(update={"position": i}) for i, e in enumerate(entries)}
    index_success = (
        index_last_success_at if index_last_success_at is not None else index_last_checked_at
    )
    return Catalog(
        schema_version=1,
        material_config_fingerprint="test",
        index=ResourceState(
            last_checked_at=index_last_checked_at,
            last_attempted_at=index_last_checked_at,
            last_success_at=index_success,
        ),
        entry_order=order,
        entries=normalized,
    )


def test_force_marks_all_force_and_fetches_index() -> None:
    fresh = NOW - timedelta(days=1)
    catalog = _refresh_catalog(
        [
            _refresh_entry("https://paulgraham.com/a.html", position=0, last_checked_at=fresh),
            _refresh_entry("https://paulgraham.com/b.html", position=1, last_checked_at=fresh),
        ],
        index_last_checked_at=fresh,
    )

    plan = plan_refresh(catalog, force=True, now=NOW, stale_after_days=STALE_AFTER)

    assert plan.fetch_index is True
    assert [d.stable_id for d in plan.decisions] == list(catalog.entry_order)
    assert all(d.fetch_page for d in plan.decisions)
    assert all(d.reasons == (RefreshReason.FORCE,) for d in plan.decisions)


def test_force_respects_max_page_fetches_by_entry_order() -> None:
    fresh = NOW - timedelta(days=1)
    catalog = _refresh_catalog(
        [
            _refresh_entry("https://paulgraham.com/a.html", position=0, last_checked_at=fresh),
            _refresh_entry("https://paulgraham.com/b.html", position=1, last_checked_at=fresh),
            _refresh_entry("https://paulgraham.com/c.html", position=2, last_checked_at=fresh),
        ],
        index_last_checked_at=fresh,
    )

    plan = plan_refresh(
        catalog,
        force=True,
        now=NOW,
        stale_after_days=STALE_AFTER,
        max_page_fetches=2,
    )

    assert [d.fetch_page for d in plan.decisions] == [True, True, False]
    assert all(d.reasons == (RefreshReason.FORCE,) for d in plan.decisions)


def test_missing_metadata_when_enrich_and_empty_summary() -> None:
    fresh = NOW - timedelta(days=1)
    catalog = _refresh_catalog(
        [
            _refresh_entry(
                "https://paulgraham.com/a.html",
                summary=None,
                last_checked_at=fresh,
            ),
            _refresh_entry(
                "https://paulgraham.com/b.html",
                summary="   ",
                last_checked_at=fresh,
            ),
            _refresh_entry(
                "https://paulgraham.com/c.html",
                summary="Has text.",
                last_checked_at=fresh,
            ),
        ],
        index_last_checked_at=fresh,
    )

    plan = plan_refresh(catalog, force=False, enrich=True, now=NOW, stale_after_days=STALE_AFTER)

    by_id = {d.stable_id: d for d in plan.decisions}
    assert by_id["https://paulgraham.com/a.html"].fetch_page is True
    assert by_id["https://paulgraham.com/a.html"].reasons == (RefreshReason.MISSING_METADATA,)
    assert by_id["https://paulgraham.com/b.html"].fetch_page is True
    assert by_id["https://paulgraham.com/b.html"].reasons == (RefreshReason.MISSING_METADATA,)
    assert by_id["https://paulgraham.com/c.html"].fetch_page is False
    assert by_id["https://paulgraham.com/c.html"].reasons == (RefreshReason.NOT_DUE,)


def test_missing_metadata_skipped_when_enrich_false() -> None:
    fresh = NOW - timedelta(days=1)
    catalog = _refresh_catalog(
        [
            _refresh_entry(
                "https://paulgraham.com/a.html",
                summary=None,
                last_checked_at=fresh,
            ),
        ],
        index_last_checked_at=fresh,
    )

    plan = plan_refresh(catalog, enrich=False, now=NOW, stale_after_days=STALE_AFTER)

    assert plan.decisions[0].fetch_page is False
    assert plan.decisions[0].reasons == (RefreshReason.NOT_DUE,)


def test_stale_when_never_checked_or_older_than_threshold() -> None:
    fresh = NOW - timedelta(days=1)
    old = NOW - timedelta(days=STALE_AFTER)
    older = NOW - timedelta(days=STALE_AFTER + 1)
    catalog = _refresh_catalog(
        [
            _refresh_entry(
                "https://paulgraham.com/never.html",
                position=0,
                last_checked_at=None,
            ),
            _refresh_entry(
                "https://paulgraham.com/boundary.html",
                position=1,
                last_checked_at=old,
            ),
            _refresh_entry(
                "https://paulgraham.com/old.html",
                position=2,
                last_checked_at=older,
            ),
            _refresh_entry(
                "https://paulgraham.com/fresh.html",
                position=3,
                last_checked_at=fresh,
            ),
        ],
        index_last_checked_at=fresh,
    )

    plan = plan_refresh(catalog, now=NOW, stale_after_days=STALE_AFTER)
    by_id = {d.stable_id: d for d in plan.decisions}

    assert by_id["https://paulgraham.com/never.html"].reasons == (RefreshReason.STALE,)
    assert by_id["https://paulgraham.com/never.html"].fetch_page is True
    assert by_id["https://paulgraham.com/boundary.html"].reasons == (RefreshReason.STALE,)
    assert by_id["https://paulgraham.com/old.html"].reasons == (RefreshReason.STALE,)
    assert by_id["https://paulgraham.com/fresh.html"].reasons == (RefreshReason.NOT_DUE,)
    assert by_id["https://paulgraham.com/fresh.html"].fetch_page is False


def test_canary_marks_selected_ids() -> None:
    fresh = NOW - timedelta(days=1)
    catalog = _refresh_catalog(
        [
            _refresh_entry("https://paulgraham.com/a.html", position=0, last_checked_at=fresh),
            _refresh_entry("https://paulgraham.com/b.html", position=1, last_checked_at=fresh),
        ],
        index_last_checked_at=fresh,
    )

    plan = plan_refresh(
        catalog,
        now=NOW,
        stale_after_days=STALE_AFTER,
        canary_ids=frozenset({"https://paulgraham.com/b.html"}),
    )
    by_id = {d.stable_id: d for d in plan.decisions}

    assert by_id["https://paulgraham.com/a.html"].reasons == (RefreshReason.NOT_DUE,)
    assert by_id["https://paulgraham.com/b.html"].reasons == (RefreshReason.CANARY,)
    assert by_id["https://paulgraham.com/b.html"].fetch_page is True


def test_multiple_reasons_accumulate_deterministically() -> None:
    catalog = _refresh_catalog(
        [
            _refresh_entry(
                "https://paulgraham.com/a.html",
                summary=None,
                last_checked_at=None,
            ),
        ],
        index_last_checked_at=NOW,
    )

    plan = plan_refresh(
        catalog,
        now=NOW,
        stale_after_days=STALE_AFTER,
        canary_ids=frozenset({"https://paulgraham.com/a.html"}),
    )

    assert plan.decisions[0].reasons == (
        RefreshReason.STALE,
        RefreshReason.MISSING_METADATA,
        RefreshReason.CANARY,
    )
    assert plan.decisions[0].fetch_page is True


def test_fetch_index_when_index_unchecked_or_stale() -> None:
    fresh = NOW - timedelta(days=1)
    old = NOW - timedelta(days=STALE_AFTER + 1)
    entry = _refresh_entry("https://paulgraham.com/a.html", last_checked_at=fresh)

    unchecked = _refresh_catalog([entry], index_last_checked_at=None)
    assert plan_refresh(unchecked, now=NOW, stale_after_days=STALE_AFTER).fetch_index is True

    stale_index = _refresh_catalog([entry], index_last_checked_at=old)
    assert plan_refresh(stale_index, now=NOW, stale_after_days=STALE_AFTER).fetch_index is True

    fresh_index = _refresh_catalog([entry], index_last_checked_at=fresh)
    assert plan_refresh(fresh_index, now=NOW, stale_after_days=STALE_AFTER).fetch_index is False


def test_decisions_follow_entry_order() -> None:
    fresh = NOW - timedelta(days=1)
    a = _refresh_entry("https://paulgraham.com/a.html", position=0, last_checked_at=fresh)
    b = _refresh_entry("https://paulgraham.com/b.html", position=1, last_checked_at=fresh)
    c = _refresh_entry("https://paulgraham.com/c.html", position=2, last_checked_at=fresh)
    catalog = Catalog(
        schema_version=1,
        material_config_fingerprint="test",
        index=ResourceState(last_checked_at=fresh),
        entry_order=[
            "https://paulgraham.com/c.html",
            "https://paulgraham.com/a.html",
            "https://paulgraham.com/b.html",
        ],
        entries={e.stable_id: e for e in (a, b, c)},
    )

    plan = plan_refresh(catalog, now=NOW, stale_after_days=STALE_AFTER)
    assert [d.stable_id for d in plan.decisions] == catalog.entry_order


def test_max_page_fetches_caps_due_entries_not_not_due() -> None:
    fresh = NOW - timedelta(days=1)
    catalog = _refresh_catalog(
        [
            _refresh_entry(
                "https://paulgraham.com/fresh.html",
                position=0,
                last_checked_at=fresh,
            ),
            _refresh_entry(
                "https://paulgraham.com/stale1.html",
                position=1,
                last_checked_at=None,
            ),
            _refresh_entry(
                "https://paulgraham.com/stale2.html",
                position=2,
                last_checked_at=None,
            ),
            _refresh_entry(
                "https://paulgraham.com/stale3.html",
                position=3,
                last_checked_at=None,
            ),
        ],
        index_last_checked_at=fresh,
    )

    plan = plan_refresh(
        catalog,
        now=NOW,
        stale_after_days=STALE_AFTER,
        max_page_fetches=1,
    )
    by_id = {d.stable_id: d for d in plan.decisions}

    assert by_id["https://paulgraham.com/fresh.html"].fetch_page is False
    assert by_id["https://paulgraham.com/stale1.html"].fetch_page is True
    assert by_id["https://paulgraham.com/stale2.html"].fetch_page is False
    assert by_id["https://paulgraham.com/stale2.html"].reasons == (RefreshReason.STALE,)
    assert by_id["https://paulgraham.com/stale3.html"].fetch_page is False


def test_max_page_fetches_zero_fetches_none() -> None:
    catalog = _refresh_catalog(
        [_refresh_entry("https://paulgraham.com/a.html", last_checked_at=None)],
        index_last_checked_at=None,
    )
    plan = plan_refresh(catalog, now=NOW, max_page_fetches=0)
    assert plan.fetch_index is True
    assert plan.decisions[0].fetch_page is False
    assert plan.decisions[0].reasons == (RefreshReason.STALE,)


def test_plan_refresh_naive_now_rejected() -> None:
    catalog = _refresh_catalog([])
    with pytest.raises(FeedError, match="Naive"):
        plan_refresh(catalog, now=datetime(2026, 7, 25, 12, 0, 0))


def test_skips_entry_order_ids_missing_from_map() -> None:
    """Catalog model forbids entry_order ids absent from entries."""
    fresh = NOW - timedelta(days=1)
    entry = _refresh_entry("https://paulgraham.com/a.html", last_checked_at=fresh)
    with pytest.raises(ValidationError, match="entry_order"):
        Catalog(
            schema_version=1,
            material_config_fingerprint="test",
            index=ResourceState(last_checked_at=fresh),
            entry_order=[
                "https://paulgraham.com/missing.html",
                "https://paulgraham.com/a.html",
            ],
            entries={entry.stable_id: entry},
        )


def test_force_overrides_other_reasons() -> None:
    catalog = _refresh_catalog(
        [
            _refresh_entry(
                "https://paulgraham.com/a.html",
                summary=None,
                last_checked_at=None,
            ),
        ],
        index_last_checked_at=None,
    )
    plan = plan_refresh(
        catalog,
        force=True,
        now=NOW,
        canary_ids=frozenset({"https://paulgraham.com/a.html"}),
    )
    assert plan.decisions[0].reasons == (RefreshReason.FORCE,)


# ---------------------------------------------------------------------------
# Atomic I/O (from test_atomic_io)
# ---------------------------------------------------------------------------


def test_atomic_write_bytes_success(tmp_path: Path) -> None:
    path = tmp_path / "out.bin"
    atomic_write_bytes(path, b"hello")
    assert path.read_bytes() == b"hello"
    assert list(tmp_path.glob(".out.bin.*")) == []


def test_atomic_write_text_success(tmp_path: Path) -> None:
    path = tmp_path / "out.txt"
    atomic_write_text(path, "café")
    assert path.read_text(encoding="utf-8") == "café"
    assert list(tmp_path.glob(".out.txt.*")) == []


def test_atomic_write_creates_parent_dirs(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "file.txt"
    atomic_write_text(path, "nested")
    assert path.read_text(encoding="utf-8") == "nested"


def test_atomic_write_overwrites_existing(tmp_path: Path) -> None:
    path = tmp_path / "file.bin"
    atomic_write_bytes(path, b"prior")
    atomic_write_bytes(path, b"next")
    assert path.read_bytes() == b"next"


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits only")
def test_atomic_write_file_mode_0644(tmp_path: Path) -> None:
    path = tmp_path / "mode.txt"
    atomic_write_text(path, "mode")
    mode = path.stat().st_mode
    assert mode & stat.S_IRUSR
    assert mode & stat.S_IRGRP, f"not group-readable: {oct(mode)}"
    assert mode & stat.S_IROTH, f"not other-readable: {oct(mode)}"
    assert not (mode & stat.S_IXUSR)


def test_atomic_write_bytes_cleans_tmp_on_replace_failure(tmp_path: Path) -> None:
    path = tmp_path / "x.bin"
    atomic_write_bytes(path, b"prior")
    prior = path.read_bytes()

    with (
        patch(
            "paul_graham_essay_feeds.catalog.os.replace",
            side_effect=OSError("disk full"),
        ),
        pytest.raises(OSError, match="disk full"),
    ):
        atomic_write_bytes(path, b"new")

    assert path.read_bytes() == prior
    assert list(tmp_path.glob(".x.bin.*")) == []


def test_atomic_write_text_cleans_tmp_on_replace_failure(tmp_path: Path) -> None:
    path = tmp_path / "x.txt"
    atomic_write_text(path, "prior")
    prior = path.read_text(encoding="utf-8")

    with (
        patch(
            "paul_graham_essay_feeds.catalog.os.replace",
            side_effect=OSError("disk"),
        ),
        pytest.raises(OSError, match="disk"),
    ):
        atomic_write_text(path, "new")

    assert path.read_text(encoding="utf-8") == prior
    assert list(tmp_path.glob(".x.txt.*")) == []


def test_planner_freshness_uses_last_success_not_failed_attempt() -> None:
    """Recent failed attempt must not keep a page fresh; success TTL is last_success_at."""
    fresh_attempt = NOW
    old_success = NOW - timedelta(days=STALE_AFTER + 5)
    catalog = _refresh_catalog(
        [
            _refresh_entry(
                "https://paulgraham.com/a.html",
                last_checked_at=fresh_attempt,
                last_success_at=old_success,
            )
        ],
        index_last_checked_at=fresh_attempt,
        index_last_success_at=NOW - timedelta(days=1),
    )
    plan = plan_refresh(catalog, enrich=True, stale_after_days=STALE_AFTER, now=NOW)
    assert plan.decisions[0].fetch_page is True
    assert RefreshReason.STALE in plan.decisions[0].reasons


def test_planner_recent_success_not_due_despite_older_attempt_gap() -> None:
    """Within-TTL last_success_at is NOT_DUE even if last_checked_at is also recent."""
    fresh = NOW - timedelta(days=1)
    catalog = _refresh_catalog(
        [_refresh_entry("https://paulgraham.com/a.html", last_checked_at=fresh)],
        index_last_checked_at=fresh,
    )
    plan = plan_refresh(catalog, enrich=True, stale_after_days=STALE_AFTER, now=NOW)
    assert plan.decisions[0].fetch_page is False
    assert plan.decisions[0].reasons == (RefreshReason.NOT_DUE,)
