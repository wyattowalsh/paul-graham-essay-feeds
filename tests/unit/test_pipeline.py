"""Unit tests for the catalog-native update pipeline (F-001)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from paul_graham_essay_feeds.catalog import (
    ChangeSet,
    RefreshPlan,
    default_catalog_path,
    empty_catalog,
    load_catalog,
    plan_refresh,
    save_catalog,
)
from paul_graham_essay_feeds.models import Essay, ResourceState
from paul_graham_essay_feeds.pipeline import (
    _material_unchanged_vs_disk,
    _should_skip_publish,
    run_catalog_pipeline,
)
from paul_graham_essay_feeds.settings import Settings
from tests.html_samples import synthetic_index_html

T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
T_LATER = T0 + timedelta(days=40)


def _settings(tmp_path: Path, **kwargs: object) -> Settings:
    data = {
        "repo_root": tmp_path,
        "min_items": 3,
        "enrich": False,
        "force": False,
        "quiet": True,
        "validate_links": False,
    }
    data.update(kwargs)
    return Settings.model_validate(data)


def _stable_enrich(essays: list[Essay], **_: object) -> list[Essay]:
    """Deterministic enrich: identical material across calls for the same essays."""
    return [
        essay.model_copy(
            update={
                "summary": f"Stable summary for {essay.title}",
                "published_hint": "January 2024",
                "content_hash": "ab" * 32,
            }
        )
        for essay in essays
    ]


def test_pipeline_publish_creates_catalog_and_feeds(tmp_path: Path) -> None:
    from paul_graham_essay_feeds.models import MIN_ITEMS

    html = synthetic_index_html()
    settings = _settings(tmp_path, min_items=MIN_ITEMS, enrich=False)
    result = run_catalog_pipeline(settings, html=html, now=T0)

    assert result.action == "updated"
    assert result.skipped is False
    assert (tmp_path / "feeds" / "feed.json").is_file()
    assert default_catalog_path(tmp_path).is_file()
    assert not (tmp_path / "state" / "current.json").exists()
    assert not (tmp_path / "state" / "generations").exists()
    assert len(result.catalog.entry_order) >= MIN_ITEMS


def test_pipeline_second_pass_skips_when_not_due(tmp_path: Path) -> None:
    from paul_graham_essay_feeds.models import MIN_ITEMS

    html = synthetic_index_html()
    settings = _settings(tmp_path, min_items=MIN_ITEMS, enrich=False)
    first = run_catalog_pipeline(settings, html=html, now=T0)
    assert first.action == "updated"

    second = run_catalog_pipeline(settings, html=html, now=T0)
    assert second.action == "unchanged"
    assert second.skipped is True


def test_pipeline_unchanged_zero_tracked_writes(tmp_path: Path) -> None:
    """Pre-enrich skip must not rewrite catalog or feeds."""
    from paul_graham_essay_feeds.models import MIN_ITEMS

    html = synthetic_index_html()
    settings = _settings(tmp_path, min_items=MIN_ITEMS, enrich=False)
    first = run_catalog_pipeline(settings, html=html, now=T0)
    assert first.action == "updated"

    catalog_path = default_catalog_path(tmp_path)
    rss_path = tmp_path / "feeds" / "rss.xml"
    atom_path = tmp_path / "feeds" / "atom.xml"
    json_path = tmp_path / "feeds" / "feed.json"

    catalog_bytes = catalog_path.read_bytes()
    rss_bytes = rss_path.read_bytes()
    atom_bytes = atom_path.read_bytes()
    json_bytes = json_path.read_bytes()
    catalog_mtime = catalog_path.stat().st_mtime_ns
    rss_mtime = rss_path.stat().st_mtime_ns

    second = run_catalog_pipeline(settings, html=html, now=T0)
    assert second.action == "unchanged"
    assert second.skipped is True

    assert catalog_path.read_bytes() == catalog_bytes
    assert rss_path.read_bytes() == rss_bytes
    assert atom_path.read_bytes() == atom_bytes
    assert json_path.read_bytes() == json_bytes
    assert catalog_path.stat().st_mtime_ns == catalog_mtime
    assert rss_path.stat().st_mtime_ns == rss_mtime


def test_post_enrich_material_noop_zero_tracked_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """STALE enrich with identical material → unchanged, zero tracked writes."""
    from paul_graham_essay_feeds.models import MIN_ITEMS

    html = synthetic_index_html()
    enrich = MagicMock(side_effect=_stable_enrich)
    monkeypatch.setattr("paul_graham_essay_feeds.pipeline.enrich_essays", enrich)

    settings = _settings(
        tmp_path,
        min_items=MIN_ITEMS,
        enrich=True,
        stale_after_days=30,
    )
    first = run_catalog_pipeline(settings, html=html, now=T0)
    assert first.action == "updated"
    assert enrich.call_count >= 1
    first_calls = enrich.call_count

    seeded = load_catalog(default_catalog_path(tmp_path))
    assert seeded is not None
    aged = {
        sid: entry.model_copy(
            update={
                "page": entry.page.model_copy(update={"last_checked_at": T0 - timedelta(days=60)})
            }
        )
        for sid, entry in seeded.entries.items()
    }
    save_catalog(
        default_catalog_path(tmp_path),
        seeded.model_copy(update={"entries": aged}),
    )

    catalog_path = default_catalog_path(tmp_path)
    rss_path = tmp_path / "feeds" / "rss.xml"
    atom_path = tmp_path / "feeds" / "atom.xml"
    json_path = tmp_path / "feeds" / "feed.json"

    catalog_bytes = catalog_path.read_bytes()
    rss_bytes = rss_path.read_bytes()
    atom_bytes = atom_path.read_bytes()
    json_bytes = json_path.read_bytes()
    catalog_mtime = catalog_path.stat().st_mtime_ns
    rss_mtime = rss_path.stat().st_mtime_ns

    second = run_catalog_pipeline(settings, html=html, now=T_LATER)
    assert second.action == "unchanged"
    assert second.skipped is True
    assert enrich.call_count > first_calls

    assert catalog_path.read_bytes() == catalog_bytes
    assert rss_path.read_bytes() == rss_bytes
    assert atom_path.read_bytes() == atom_bytes
    assert json_path.read_bytes() == json_bytes
    assert catalog_path.stat().st_mtime_ns == catalog_mtime
    assert rss_path.stat().st_mtime_ns == rss_mtime


def test_post_enrich_material_change_still_publishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When re-enrich changes material, publish must still run."""
    from paul_graham_essay_feeds.models import MIN_ITEMS

    html = synthetic_index_html()
    call_n = {"n": 0}

    def evolving_enrich(essays: list[Essay], **_: object) -> list[Essay]:
        call_n["n"] += 1
        suffix = "v1" if call_n["n"] == 1 else "v2"
        return [
            essay.model_copy(
                update={
                    "summary": f"Summary {suffix} for {essay.title}",
                    "published_hint": "January 2024",
                    "content_hash": ("cd" if call_n["n"] == 1 else "ef") * 32,
                }
            )
            for essay in essays
        ]

    monkeypatch.setattr(
        "paul_graham_essay_feeds.pipeline.enrich_essays",
        evolving_enrich,
    )
    settings = _settings(
        tmp_path,
        min_items=MIN_ITEMS,
        enrich=True,
        stale_after_days=30,
    )
    first = run_catalog_pipeline(settings, html=html, now=T0)
    assert first.action == "updated"
    first_sid = next(iter(first.catalog.entry_order))
    first_summary = first.catalog.entries[first_sid].summary

    seeded = load_catalog(default_catalog_path(tmp_path))
    assert seeded is not None
    aged = {
        sid: entry.model_copy(
            update={
                "page": entry.page.model_copy(update={"last_checked_at": T0 - timedelta(days=60)})
            }
        )
        for sid, entry in seeded.entries.items()
    }
    save_catalog(
        default_catalog_path(tmp_path),
        seeded.model_copy(update={"entries": aged}),
    )

    second = run_catalog_pipeline(settings, html=html, now=T_LATER)
    assert second.action == "updated"
    assert second.skipped is False
    second_summary = second.catalog.entries[first_sid].summary
    assert second_summary != first_summary


def test_material_unchanged_helper_false_without_catalog(tmp_path: Path) -> None:
    catalog = empty_catalog(material_config_fingerprint="test")
    assert (
        _material_unchanged_vs_disk(
            tmp_path,
            catalog=catalog,
            rss=b"<rss/>",
            atom=b"<feed/>",
            json_feed=b"{}",
        )
        is False
    )


def test_f001_missing_metadata_does_not_skip_when_enrich_on(tmp_path: Path) -> None:
    """Page-only / missing summary must plan fetch even if feeds already exist."""
    from paul_graham_essay_feeds.models import MIN_ITEMS

    html = synthetic_index_html()
    settings = _settings(tmp_path, min_items=MIN_ITEMS, enrich=False)
    seeded = run_catalog_pipeline(settings, html=html, now=T0)
    assert seeded.action == "updated"

    entries = {
        sid: entry.model_copy(
            update={
                "summary": None,
                "prior_good_summary": None,
                "page": ResourceState(last_checked_at=None),
            }
        )
        for sid, entry in seeded.catalog.entries.items()
    }
    cat = seeded.catalog.model_copy(update={"entries": entries})
    save_catalog(default_catalog_path(tmp_path), cat)

    plan = plan_refresh(
        cat,
        force=False,
        enrich=True,
        stale_after_days=30,
        now=T0,
    )
    assert any(d.fetch_page for d in plan.decisions)
    assert not _should_skip_publish(
        root=tmp_path,
        force=False,
        changeset=ChangeSet(),
        plan=plan,
    )


def test_should_skip_requires_catalog(tmp_path: Path) -> None:
    plan = RefreshPlan(fetch_index=False, decisions=[])
    assert (
        _should_skip_publish(
            root=tmp_path,
            force=False,
            changeset=ChangeSet(),
            plan=plan,
        )
        is False
    )


def test_legacy_index_skip_helper_removed() -> None:
    """T1 deleted CLI index-hash skip; planner owns the skip contract."""
    import paul_graham_essay_feeds.cli as cli_mod

    assert not hasattr(cli_mod, "_should_skip_update")


def test_pipeline_force_bypasses_pre_enrich_skip(tmp_path: Path) -> None:
    from paul_graham_essay_feeds.models import MIN_ITEMS

    html = synthetic_index_html()
    settings = _settings(tmp_path, min_items=MIN_ITEMS, enrich=False)
    first = run_catalog_pipeline(settings, html=html, now=T0)
    assert first.action == "updated"

    forced = _settings(tmp_path, min_items=MIN_ITEMS, enrich=False, force=True)
    second = run_catalog_pipeline(forced, html=html, now=T0)
    assert second.action == "updated"
    assert second.skipped is False
    assert default_catalog_path(tmp_path).is_file()


def test_pipeline_tombstone_prevents_skip(tmp_path: Path) -> None:
    """Index losing an essay yields tombstone candidates → must not pre-skip."""
    settings = _settings(tmp_path, min_items=3, enrich=False)
    html3 = synthetic_index_html(essay_count=1)
    first = run_catalog_pipeline(settings, html=html3, now=T0)
    assert first.action == "updated"

    seeded = load_catalog(default_catalog_path(tmp_path))
    assert seeded is not None
    ghost_id = "https://paulgraham.com/ghost.html"
    ghost = next(iter(seeded.entries.values())).model_copy(
        update={
            "stable_id": ghost_id,
            "url": ghost_id,
            "title": "Ghost Essay",
            "position": 99,
        }
    )
    entries = dict(seeded.entries)
    entries[ghost_id] = ghost
    save_catalog(
        default_catalog_path(tmp_path),
        seeded.model_copy(
            update={
                "entries": entries,
                "entry_order": [ghost_id, *seeded.entry_order],
            }
        ),
    )

    second = run_catalog_pipeline(settings, html=html3, now=T0)
    assert ghost_id in second.changeset.tombstone_candidates
    assert second.action == "updated"
    assert second.skipped is False


def test_prior_good_retained_when_enrich_returns_fffd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paul_graham_essay_feeds.models import MIN_ITEMS

    html = synthetic_index_html()
    call_n = {"n": 0}

    def enrich_then_corrupt(essays: list[Essay], **_: object) -> list[Essay]:
        call_n["n"] += 1
        if call_n["n"] == 1:
            return _stable_enrich(essays)
        return [
            essay.model_copy(
                update={
                    "summary": "Broken \ufffd summary that must not replace prior-good.",
                    "published_hint": "January 2024",
                    "content_hash": "ff" * 32,
                }
            )
            for essay in essays
        ]

    monkeypatch.setattr(
        "paul_graham_essay_feeds.pipeline.enrich_essays",
        enrich_then_corrupt,
    )
    settings = _settings(
        tmp_path,
        min_items=MIN_ITEMS,
        enrich=True,
        stale_after_days=30,
    )
    first = run_catalog_pipeline(settings, html=html, now=T0)
    assert first.action == "updated"
    good = next(iter(first.catalog.entries.values())).prior_good_summary
    assert good is not None
    assert "\ufffd" not in good

    seeded = load_catalog(default_catalog_path(tmp_path))
    assert seeded is not None
    aged = {
        sid: entry.model_copy(
            update={
                "page": entry.page.model_copy(update={"last_checked_at": T0 - timedelta(days=60)})
            }
        )
        for sid, entry in seeded.entries.items()
    }
    save_catalog(
        default_catalog_path(tmp_path),
        seeded.model_copy(update={"entries": aged}),
    )

    second = run_catalog_pipeline(settings, html=html, now=T_LATER)
    sample = next(iter(second.catalog.entries.values()))
    assert sample.summary == good
    assert sample.prior_good_summary == good
    assert "\ufffd" not in (sample.summary or "")


def test_pipeline_force_true_in_should_skip_helper(tmp_path: Path) -> None:
    plan = RefreshPlan(fetch_index=False, decisions=[])
    (tmp_path / "feeds").mkdir()
    for name in ("rss.xml", "atom.xml", "feed.json"):
        (tmp_path / "feeds" / name).write_text("x", encoding="utf-8")
    default_catalog_path(tmp_path).write_text(
        '{"schema_version": 1, "material_config_fingerprint": "x", '
        '"entry_order": [], "entries": {}}\n',
        encoding="utf-8",
    )
    assert (
        _should_skip_publish(
            root=tmp_path,
            force=True,
            changeset=ChangeSet(),
            plan=plan,
        )
        is False
    )


def test_index_304_plan_only_skips_enrich(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """304 with no page-due → unchanged and zero enrich GETs."""
    from paul_graham_essay_feeds.http import IndexFetchResult
    from paul_graham_essay_feeds.models import MIN_ITEMS

    html = synthetic_index_html()
    enrich = MagicMock(side_effect=_stable_enrich)
    monkeypatch.setattr("paul_graham_essay_feeds.pipeline.enrich_essays", enrich)

    settings = _settings(tmp_path, min_items=MIN_ITEMS, enrich=True)
    first = run_catalog_pipeline(settings, html=html, now=T0)
    assert first.action == "updated"
    first_calls = enrich.call_count
    assert first_calls >= 1

    monkeypatch.setattr(
        "paul_graham_essay_feeds.pipeline.fetch_index",
        MagicMock(
            return_value=IndexFetchResult(
                html=None,
                not_modified=True,
                etag='"idx-1"',
                last_modified="Mon, 01 Jan 2024 00:00:00 GMT",
                status_code=304,
            )
        ),
    )
    second = run_catalog_pipeline(settings, now=T0)
    assert second.action == "unchanged"
    assert enrich.call_count == first_calls
    cache = tmp_path / ".cache" / "http-cache.json"
    assert cache.is_file()
    assert "idx-1" in cache.read_text(encoding="utf-8")


def test_index_304_page_due_still_enriches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """304 does not block F-001 page-due enrich."""
    from paul_graham_essay_feeds.http import IndexFetchResult
    from paul_graham_essay_feeds.models import MIN_ITEMS

    html = synthetic_index_html()
    enrich = MagicMock(side_effect=_stable_enrich)
    monkeypatch.setattr("paul_graham_essay_feeds.pipeline.enrich_essays", enrich)
    settings = _settings(
        tmp_path,
        min_items=MIN_ITEMS,
        enrich=True,
        stale_after_days=30,
    )
    first = run_catalog_pipeline(settings, html=html, now=T0)
    assert first.action == "updated"
    first_calls = enrich.call_count
    assert first_calls >= 1

    seeded = load_catalog(default_catalog_path(tmp_path))
    assert seeded is not None
    aged = {
        sid: entry.model_copy(
            update={
                "page": entry.page.model_copy(update={"last_checked_at": T0 - timedelta(days=60)})
            }
        )
        for sid, entry in seeded.entries.items()
    }
    save_catalog(
        default_catalog_path(tmp_path),
        seeded.model_copy(update={"entries": aged}),
    )

    monkeypatch.setattr(
        "paul_graham_essay_feeds.pipeline.fetch_index",
        MagicMock(
            return_value=IndexFetchResult(
                html=None,
                not_modified=True,
                etag='"idx-2"',
                status_code=304,
            )
        ),
    )
    second = run_catalog_pipeline(settings, now=T_LATER)
    assert enrich.call_count > first_calls
    assert second.refresh_plan is not None
    assert any(d.fetch_page for d in second.refresh_plan.decisions)


def test_default_catalog_path_is_repo_root() -> None:
    root = Path("/tmp/repo")
    assert default_catalog_path(root) == root / "catalog.json"
