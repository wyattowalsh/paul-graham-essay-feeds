"""Unit tests for the catalog-native update pipeline (F-001)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TypedDict
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
from paul_graham_essay_feeds.feeds import all_feed_paths
from paul_graham_essay_feeds.models import Catalog, CatalogEntry, Essay, ResourceState
from paul_graham_essay_feeds.pipeline import (
    _apply_enrichment,
    _complete_index_state,
    _material_unchanged_vs_disk,
    _save_catalog_under_lock,
    _should_skip_publish,
    material_catalog_digest,
    run_catalog_pipeline,
)
from paul_graham_essay_feeds.settings import Settings
from tests.html_samples import synthetic_index_html

T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
T_LATER = T0 + timedelta(days=40)


class _LockFeedBytes(TypedDict):
    rss: bytes
    atom: bytes
    json_feed: bytes
    simple_rss: bytes
    simple_atom: bytes
    simple_json_feed: bytes


_LOCK_FEED_BYTES = _LockFeedBytes(
    rss=b"<rss/>",
    atom=b"<feed/>",
    json_feed=b"{}",
    simple_rss=b"<rss/>",
    simple_atom=b"<feed/>",
    simple_json_feed=b"{}",
)


def _clock_catalog(
    *,
    last_success: datetime,
    last_seen: datetime,
    generation_id: str | None,
    cursor: str,
    title: str = "A",
    summary: str = "Short summary content for tests.",
) -> Catalog:
    entry = CatalogEntry(
        stable_id="https://paulgraham.com/a.html",
        url="https://paulgraham.com/a.html",
        title=title,
        position=0,
        last_seen_at=last_seen,
        observed_updated_at=T0,
        summary=summary,
        page=ResourceState(last_success_at=last_success),
    )
    return Catalog(
        schema_version=2,
        material_config_fingerprint="test",
        versions={"page_fetch_cursor": cursor},
        index=ResourceState(last_success_at=last_success),
        entry_order=[entry.stable_id],
        entries={entry.stable_id: entry},
        last_generation_id=generation_id,
    )


def _write_public_artifacts(
    root: Path,
    catalog: Catalog,
    *,
    rss: bytes,
    atom: bytes,
    json_feed: bytes,
    simple_rss: bytes,
    simple_atom: bytes,
    simple_json_feed: bytes,
) -> None:
    """Write catalog.json plus six feed files without a recover pointer."""
    save_catalog(default_catalog_path(root), catalog)
    paths = all_feed_paths(root)
    paths["rss"].parent.mkdir(parents=True, exist_ok=True)
    paths["rss"].write_bytes(rss)
    paths["atom"].write_bytes(atom)
    paths["json"].write_bytes(json_feed)
    paths["rss_simple"].write_bytes(simple_rss)
    paths["atom_simple"].write_bytes(simple_atom)
    paths["json_simple"].write_bytes(simple_json_feed)


def _write_pending_generation(
    root: Path,
    catalog: Catalog,
    *,
    rss: bytes,
    atom: bytes,
    json_feed: bytes,
    simple_rss: bytes,
    simple_atom: bytes,
    simple_json_feed: bytes,
) -> str:
    from paul_graham_essay_feeds.publication import write_staging_generation

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
    pointer = root / ".cache" / "materialize.json"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(
        json.dumps({"gen_id": gen_id, "phase": "materializing"}, indent=2) + "\n",
        encoding="utf-8",
    )
    return gen_id


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


def _stable_enrich(essays: list[Essay], **kwargs: object) -> list[Essay]:
    """Deterministic enrich: identical material across calls for the same essays."""
    from paul_graham_essay_feeds.enrich import PageEnrichEvidence

    out = [
        essay.model_copy(
            update={
                "summary": f"Stable summary for {essay.title}",
                "published_hint": "January 2024",
                "content_hash": "ab" * 32,
            }
        )
        for essay in essays
    ]
    page_evidence_out = kwargs.get("page_evidence_out")
    if page_evidence_out is not None:
        for essay in out:
            page_evidence_out[essay.stable_id] = PageEnrichEvidence(  # type: ignore[index]
                ok=True, status_code=200
            )
    return out


def test_pipeline_publish_creates_catalog_and_feeds(tmp_path: Path) -> None:
    from paul_graham_essay_feeds.models import MIN_ITEMS

    html = synthetic_index_html()
    settings = _settings(tmp_path, min_items=MIN_ITEMS, enrich=False)
    result = run_catalog_pipeline(settings, html=html, now=T0)

    assert result.action == "updated"
    assert result.skipped is False
    assert (tmp_path / "feeds" / "feed.json").is_file()
    assert (tmp_path / "feeds" / "feed.simple.json").is_file()
    assert default_catalog_path(tmp_path).is_file()
    assert not (tmp_path / "state" / "current.json").exists()
    assert not (tmp_path / "state" / "generations").exists()
    assert len(result.catalog.entry_order) >= MIN_ITEMS
    index = result.catalog.index
    assert index.last_checked_at == T0
    assert index.last_attempted_at == T0
    assert index.last_response_at == T0
    assert index.last_success_at == T0
    assert index.failure_count == 0
    assert index.status_code == 200


def test_pipeline_enriched_summaries_when_enrich_on(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enriched feeds include catalog summaries (not title blurbs only)."""
    import json

    from paul_graham_essay_feeds.models import MIN_ITEMS, blurb

    html = synthetic_index_html()
    enrich = MagicMock(side_effect=_stable_enrich)
    monkeypatch.setattr("paul_graham_essay_feeds.pipeline.enrich_essays", enrich)
    settings = _settings(tmp_path, min_items=MIN_ITEMS, enrich=True)
    result = run_catalog_pipeline(settings, html=html, now=T0)
    assert result.action == "updated"

    enriched = json.loads((tmp_path / "feeds" / "feed.json").read_text(encoding="utf-8"))
    assert enriched["items"]
    for e_item in enriched["items"]:
        summary = e_item.get("summary") or e_item.get("content_text")
        assert summary is not None
        assert summary.startswith("Stable summary for")
        assert summary != blurb(e_item["title"])
    assert (tmp_path / "feeds" / "feed.simple.json").is_file()


def test_live_probes_skip_urls_due_for_enrich(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When enrich will GET a URL, dedicated probes skip it (enrich implies reachability)."""
    from loguru import logger

    from paul_graham_essay_feeds.models import MIN_ITEMS

    html = synthetic_index_html()
    validate = MagicMock(return_value=None)
    enrich = MagicMock(side_effect=_stable_enrich)
    monkeypatch.setattr("paul_graham_essay_feeds.pipeline.validate_essays_live", validate)
    monkeypatch.setattr("paul_graham_essay_feeds.pipeline.enrich_essays", enrich)

    messages: list[str] = []
    sink_id = logger.add(lambda m: messages.append(str(m)), level="INFO")
    try:
        settings = _settings(
            tmp_path,
            min_items=MIN_ITEMS,
            enrich=True,
            validate_links=True,
            force=True,
        )
        result = run_catalog_pipeline(settings, html=html, now=T0)
    finally:
        logger.remove(sink_id)

    assert result.action == "updated"
    enrich.assert_called_once()
    validate.assert_called_once()
    probed = validate.call_args.args[0]
    enriched = enrich.call_args.args[0]
    probed_ids = {e.stable_id for e in probed}
    enriched_ids = {e.stable_id for e in enriched}
    assert probed_ids.isdisjoint(enriched_ids)
    # First force+enrich run: every essay is typically due → probe list empty.
    assert probed_ids == set() or enriched_ids
    joined = "\n".join(messages)
    assert "Skipping dedicated" not in joined
    assert "Checking and enriching" in joined


def test_live_probes_cover_all_when_enrich_off(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With --no-enrich, live probes check every essay URL."""
    from paul_graham_essay_feeds.models import MIN_ITEMS

    html = synthetic_index_html()
    validate = MagicMock(return_value=None)
    monkeypatch.setattr("paul_graham_essay_feeds.pipeline.validate_essays_live", validate)

    settings = _settings(
        tmp_path,
        min_items=MIN_ITEMS,
        enrich=False,
        validate_links=True,
    )
    result = run_catalog_pipeline(settings, html=html, now=T0)
    assert result.action == "updated"
    validate.assert_called_once()
    probed = validate.call_args.args[0]
    assert len(probed) == result.essay_count


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


def test_post_enrich_material_noop_persists_clocks_feeds_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """STALE enrich with identical material → catalog clocks saved; feed bytes untouched.

    A third pass with fresh clocks must not re-fetch (planner sees FRESH).
    """
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

    rss_bytes = rss_path.read_bytes()
    atom_bytes = atom_path.read_bytes()
    json_bytes = json_path.read_bytes()
    rss_mtime = rss_path.stat().st_mtime_ns

    second = run_catalog_pipeline(settings, html=html, now=T_LATER)
    # Catalog clocks written; feed bytes identical → state_changed (not unchanged).
    assert second.action == "state_changed"
    assert second.changed_paths == ("catalog.json",)
    assert second.skipped is True
    assert enrich.call_count > first_calls
    second_calls = enrich.call_count

    # Clocks persisted to catalog; feed projection bytes unchanged.
    reloaded = load_catalog(catalog_path)
    assert reloaded is not None
    for entry in reloaded.entries.values():
        assert entry.page.last_checked_at is not None
        assert entry.page.last_checked_at >= T_LATER - timedelta(days=1)
    assert rss_path.read_bytes() == rss_bytes
    assert atom_path.read_bytes() == atom_bytes
    assert json_path.read_bytes() == json_bytes
    assert rss_path.stat().st_mtime_ns == rss_mtime

    # Fresh clocks → multi-pass must not re-enrich.
    third = run_catalog_pipeline(settings, html=html, now=T_LATER)
    assert third.action == "unchanged"
    assert third.changed_paths == ()
    assert third.skipped is True
    assert enrich.call_count == second_calls


def test_hard_delete_then_rediscover_republishes(tmp_path: Path) -> None:
    """Essay leaving then returning to the index must republish feeds."""
    import json

    from paul_graham_essay_feeds.models import make_stable_id
    from tests.html_samples import MARKER

    settings = _settings(tmp_path, min_items=3, enrich=False)
    target_sid, _ = make_stable_id("https://paulgraham.com/essay-0.html")
    protected = (
        f'<img src="{MARKER}">'
        '<a href="https://sep.turbifycdn.com/ty/cdn/paulgraham/acl1.txt?t=1">'
        "Chapter 1 of Ansi Common Lisp</a>"
        f'<img src="{MARKER}">'
        '<a href="https://sep.turbifycdn.com/ty/cdn/paulgraham/acl2.txt?t=1">'
        "Chapter 2 of Ansi Common Lisp</a>"
    )

    full_html = (
        f'<img src="{MARKER}"><a href="essay-0.html">Essay 0</a>'
        f'<img src="{MARKER}"><a href="essay-1.html">Essay 1</a>'
        f"{protected}"
    )
    reduced_html = (
        f'<img src="{MARKER}"><a href="essay-1.html">Essay 1</a>'
        f'<img src="{MARKER}"><a href="essay-2.html">Essay 2</a>'
        f"{protected}"
    )

    first = run_catalog_pipeline(settings, html=full_html, now=T0)
    assert first.action == "updated"
    assert target_sid in first.catalog.entry_order

    second = run_catalog_pipeline(settings, html=reduced_html, now=T0)
    assert second.action == "updated"
    assert target_sid not in second.catalog.entries
    assert target_sid in second.changeset.removed
    feed_ids = {
        item["id"]
        for item in json.loads((tmp_path / "feeds" / "feed.json").read_text(encoding="utf-8"))[
            "items"
        ]
    }
    assert target_sid not in feed_ids
    assert (tmp_path / "feeds" / "feed.simple.json").is_file()

    third = run_catalog_pipeline(settings, html=full_html, now=T_LATER)
    assert third.action == "updated"
    assert third.skipped is False
    assert target_sid in third.changeset.added
    feed_ids = {
        item["id"]
        for item in json.loads((tmp_path / "feeds" / "feed.json").read_text(encoding="utf-8"))[
            "items"
        ]
    }
    assert target_sid in feed_ids


def test_post_enrich_material_change_still_publishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When re-enrich changes material, publish must still run."""
    from paul_graham_essay_feeds.models import MIN_ITEMS

    html = synthetic_index_html()
    call_n = {"n": 0}

    def evolving_enrich(essays: list[Essay], **kwargs: object) -> list[Essay]:
        from paul_graham_essay_feeds.enrich import PageEnrichEvidence

        call_n["n"] += 1
        suffix = "v1" if call_n["n"] == 1 else "v2"
        out = [
            essay.model_copy(
                update={
                    "summary": f"Summary {suffix} for {essay.title}",
                    "published_hint": "January 2024",
                    "content_hash": ("cd" if call_n["n"] == 1 else "ef") * 32,
                }
            )
            for essay in essays
        ]
        page_evidence_out = kwargs.get("page_evidence_out")
        if page_evidence_out is not None:
            for essay in out:
                page_evidence_out[essay.stable_id] = PageEnrichEvidence(  # type: ignore[index]
                    ok=True, status_code=200
                )
        return out

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
            simple_rss=b"<rss/>",
            simple_atom=b"<feed/>",
            simple_json_feed=b"{}",
        )
        is False
    )


def test_material_unchanged_false_when_feeds_missing(tmp_path: Path) -> None:
    """Missing feeds/ must not be treated as unchanged."""
    from paul_graham_essay_feeds.models import MIN_ITEMS

    html = synthetic_index_html()
    settings = _settings(tmp_path, min_items=MIN_ITEMS, enrich=False)
    result = run_catalog_pipeline(settings, html=html, now=T0)
    assert result.action == "updated"

    import shutil

    shutil.rmtree(tmp_path / "feeds")
    assert (
        _material_unchanged_vs_disk(
            tmp_path,
            catalog=result.catalog,
            rss=b"<rss/>",
            atom=b"<feed/>",
            json_feed=b"{}",
            simple_rss=b"<rss/>",
            simple_atom=b"<feed/>",
            simple_json_feed=b"{}",
        )
        is False
    )


def test_missing_feeds_prevents_pre_enrich_skip(tmp_path: Path) -> None:
    from paul_graham_essay_feeds.models import MIN_ITEMS

    html = synthetic_index_html()
    settings = _settings(tmp_path, min_items=MIN_ITEMS, enrich=False)
    first = run_catalog_pipeline(settings, html=html, now=T0)
    assert first.action == "updated"

    import shutil

    shutil.rmtree(tmp_path / "feeds")
    second = run_catalog_pipeline(settings, html=html, now=T0)
    assert second.action == "updated"
    assert second.skipped is False
    assert (tmp_path / "feeds" / "feed.json").is_file()


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


def test_pipeline_removed_prevents_skip(tmp_path: Path) -> None:
    """Index losing an essay yields changeset.removed → must not pre-skip."""
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
    assert ghost_id in second.changeset.removed
    assert ghost_id not in second.catalog.entries
    assert second.action == "updated"
    assert second.skipped is False


def test_prior_good_retained_when_enrich_returns_fffd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paul_graham_essay_feeds.models import MIN_ITEMS

    html = synthetic_index_html()
    call_n = {"n": 0}

    def enrich_then_corrupt(essays: list[Essay], **kwargs: object) -> list[Essay]:
        from paul_graham_essay_feeds.enrich import PageEnrichEvidence

        call_n["n"] += 1
        if call_n["n"] == 1:
            return _stable_enrich(essays, **kwargs)
        out = [
            essay.model_copy(
                update={
                    "summary": "Broken \ufffd summary that must not replace prior-good.",
                    "published_hint": "January 2024",
                    "content_hash": "ff" * 32,
                }
            )
            for essay in essays
        ]
        page_evidence_out = kwargs.get("page_evidence_out")
        if page_evidence_out is not None:
            for essay in out:
                page_evidence_out[essay.stable_id] = PageEnrichEvidence(  # type: ignore[index]
                    ok=True, status_code=200
                )
        return out

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
    for name in (
        "rss.xml",
        "atom.xml",
        "feed.json",
        "rss.simple.xml",
        "atom.simple.xml",
        "feed.simple.json",
    ):
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


def test_save_catalog_under_lock_recovers_then_saves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Matching post-lock disk overlays clocks onto the reloaded catalog (RV-R-001)."""
    order: list[str] = []
    disk = _clock_catalog(
        last_success=T0,
        last_seen=T0,
        generation_id="gen-g0",
        cursor="0",
    )
    later = _clock_catalog(
        last_success=T_LATER,
        last_seen=T_LATER,
        generation_id=None,
        cursor="3",
    )
    _write_public_artifacts(tmp_path, disk, **_LOCK_FEED_BYTES)
    original_save = save_catalog

    monkeypatch.setattr(
        "paul_graham_essay_feeds.publication.acquire_write_lock",
        lambda root, **kwargs: order.append("acquire") or (tmp_path / "write.lock"),
    )
    monkeypatch.setattr(
        "paul_graham_essay_feeds.publication.recover_materialize",
        lambda root: order.append("recover") or False,
    )

    def _save(path: Path, catalog: Catalog) -> None:
        order.append("save")
        original_save(path, catalog)

    monkeypatch.setattr("paul_graham_essay_feeds.pipeline.save_catalog", _save)
    monkeypatch.setattr(
        "paul_graham_essay_feeds.publication.release_write_lock",
        lambda lock: order.append("release"),
    )

    committed = _save_catalog_under_lock(tmp_path, later, **_LOCK_FEED_BYTES)
    assert committed.action == "state_changed"
    assert committed.catalog is not later
    assert committed.catalog.last_generation_id == "gen-g0"
    assert committed.catalog.index.last_success_at == T_LATER
    assert order == ["acquire", "recover", "save", "release"]
    on_disk = load_catalog(default_catalog_path(tmp_path))
    assert on_disk is not None
    assert on_disk.last_generation_id == "gen-g0"
    assert on_disk.index.last_success_at == T_LATER


def test_save_catalog_under_lock_recover_true_overlays_reloaded_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RV-C-001: recover True + matching material saves clocks onto reloaded G1."""
    pre = _clock_catalog(
        last_success=T_LATER,
        last_seen=T_LATER,
        generation_id=None,
        cursor="3",
    )
    g1 = _clock_catalog(
        last_success=T0,
        last_seen=T0,
        generation_id="gen-g1",
        cursor="0",
    )
    saved_catalogs: list[Catalog] = []
    original_save = save_catalog

    def _capture_save(path: Path, catalog: Catalog) -> None:
        saved_catalogs.append(catalog)
        original_save(path, catalog)

    monkeypatch.setattr("paul_graham_essay_feeds.pipeline.save_catalog", _capture_save)
    _write_pending_generation(tmp_path, g1, **_LOCK_FEED_BYTES)
    committed = _save_catalog_under_lock(tmp_path, pre, **_LOCK_FEED_BYTES)

    saved = committed.catalog
    assert committed.action == "state_changed"
    assert saved is not pre
    assert saved_catalogs == [saved]
    assert saved.last_generation_id == "gen-g1"
    assert saved.index.last_success_at == T_LATER
    assert saved.versions["page_fetch_cursor"] == "3"
    entry = next(iter(saved.entries.values()))
    assert entry.last_seen_at == T_LATER
    assert entry.page.last_success_at == T_LATER
    on_disk = load_catalog(default_catalog_path(tmp_path))
    assert on_disk is not None
    assert on_disk.last_generation_id == "gen-g1"
    assert on_disk.index.last_success_at == T_LATER


def test_save_catalog_under_lock_recover_true_divergent_material_does_not_save_pre(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RV-C-001: recover True + different feeds publishes this-run bytes under lock."""
    pre = _clock_catalog(
        last_success=T_LATER,
        last_seen=T_LATER,
        generation_id=None,
        cursor="3",
    )
    g1 = _clock_catalog(
        last_success=T0,
        last_seen=T0,
        generation_id="gen-g1",
        cursor="0",
    )
    poison = b"<rss>G1-POISON</rss>"
    _write_pending_generation(
        tmp_path,
        g1,
        rss=poison,
        atom=poison,
        json_feed=poison,
        simple_rss=poison,
        simple_atom=poison,
        simple_json_feed=poison,
    )
    committed = _save_catalog_under_lock(tmp_path, pre, **_LOCK_FEED_BYTES)

    assert committed.action == "updated"
    assert committed.catalog is pre
    on_disk = load_catalog(default_catalog_path(tmp_path))
    assert on_disk is not None
    assert on_disk.index.last_success_at == T_LATER
    assert on_disk.last_generation_id != "gen-g1"
    assert (tmp_path / "feeds" / "rss.xml").read_bytes() == _LOCK_FEED_BYTES["rss"]
    assert (tmp_path / "feeds" / "atom.xml").read_bytes() == _LOCK_FEED_BYTES["atom"]


def test_save_catalog_under_lock_no_recovery_concurrent_title_change(tmp_path: Path) -> None:
    """PGF-P0-001: clean concurrent publisher, same IDs, different title/summary."""
    candidate = _clock_catalog(
        last_success=T_LATER,
        last_seen=T_LATER,
        generation_id=None,
        cursor="3",
        title="A",
        summary="Short summary content for tests.",
    )
    g0 = _clock_catalog(
        last_success=T0,
        last_seen=T0,
        generation_id="gen-g0",
        cursor="0",
        title="A",
        summary="Short summary content for tests.",
    )
    g1 = _clock_catalog(
        last_success=T0,
        last_seen=T0,
        generation_id="gen-g1",
        cursor="1",
        title="B-changed",
        summary="Different summary proving ID-only parity is not enough.",
    )
    _write_public_artifacts(tmp_path, g0, **_LOCK_FEED_BYTES)
    assert _material_unchanged_vs_disk(tmp_path, catalog=candidate, **_LOCK_FEED_BYTES)
    assert g0.entry_order == g1.entry_order
    assert material_catalog_digest(g0) != material_catalog_digest(g1)

    _write_public_artifacts(tmp_path, g1, **_LOCK_FEED_BYTES)
    assert not (tmp_path / ".cache" / "materialize.json").exists()
    assert not _material_unchanged_vs_disk(tmp_path, catalog=candidate, **_LOCK_FEED_BYTES)

    committed = _save_catalog_under_lock(tmp_path, candidate, **_LOCK_FEED_BYTES)
    assert committed.action == "updated"
    on_disk = load_catalog(default_catalog_path(tmp_path))
    assert on_disk is not None
    entry = next(iter(on_disk.entries.values()))
    assert entry.title == "A"
    assert entry.summary == "Short summary content for tests."
    assert material_catalog_digest(on_disk) == material_catalog_digest(candidate)
    assert (tmp_path / "feeds" / "rss.xml").read_bytes() == _LOCK_FEED_BYTES["rss"]


def test_complete_index_state_200_and_304_advance_success_and_clear_failure() -> None:
    prior = ResourceState(
        etag='"old"',
        last_modified="Tue, 01 Jul 2024 00:00:00 GMT",
        raw_sha256="a" * 64,
        decoded_sha256="b" * 64,
        last_checked_at=T0,
        last_attempted_at=T0,
        last_response_at=T0,
        last_success_at=T0,
        failure_count=3,
        last_error_kind="timeout",
        last_error_message="timed out",
        next_retry_at=T_LATER,
        status_code=503,
        selected_encoding="windows-1252",
    )
    state_200 = _complete_index_state(
        prior=prior,
        observed=T_LATER,
        etag='"new"',
        last_modified="Wed, 02 Jul 2024 00:00:00 GMT",
        raw_sha256="c" * 64,
        decoded_sha256="d" * 64,
        status_code=200,
    )
    assert state_200.last_checked_at == T_LATER
    assert state_200.last_attempted_at == T_LATER
    assert state_200.last_response_at == T_LATER
    assert state_200.last_success_at == T_LATER
    assert state_200.failure_count == 0
    assert state_200.last_error_kind is None
    assert state_200.last_error_message is None
    assert state_200.next_retry_at is None
    assert state_200.selected_encoding == "windows-1252"
    assert state_200.raw_sha256 == "c" * 64
    assert state_200.status_code == 200

    state_304 = _complete_index_state(
        prior=prior,
        observed=T_LATER,
        etag='"old"',
        last_modified="Tue, 01 Jul 2024 00:00:00 GMT",
        raw_sha256=None,
        decoded_sha256=prior.decoded_sha256,
        status_code=304,
    )
    assert state_304.last_success_at == T_LATER
    assert state_304.last_checked_at == T_LATER
    assert state_304.last_attempted_at == T_LATER
    assert state_304.raw_sha256 == prior.raw_sha256
    assert state_304.decoded_sha256 == prior.decoded_sha256
    assert state_304.failure_count == 0
    assert state_304.status_code == 304


def test_complete_index_state_local_html_has_coherent_success() -> None:
    prior = ResourceState()
    state = _complete_index_state(
        prior=prior,
        observed=T_LATER,
        etag=None,
        last_modified=None,
        raw_sha256=None,
        decoded_sha256="e" * 64,
        status_code=200,
    )
    assert state.last_checked_at == T_LATER
    assert state.last_attempted_at == T_LATER
    assert state.last_response_at == T_LATER
    assert state.last_success_at == T_LATER
    assert state.raw_sha256 is None
    assert state.decoded_sha256 == "e" * 64
    assert state.failure_count == 0


def test_apply_enrichment_failure_advances_attempt_not_success() -> None:
    from paul_graham_essay_feeds.enrich import PageEnrichEvidence

    sid = "https://paulgraham.com/a.html"
    entry = CatalogEntry(
        stable_id=sid,
        url=sid,
        title="A",
        position=0,
        summary="prior good",
        prior_good_summary="prior good",
        observed_updated_at=T0,
        page=ResourceState(
            last_checked_at=T0,
            last_attempted_at=T0,
            last_response_at=T0,
            last_success_at=T0,
            failure_count=0,
            status_code=200,
        ),
    )
    catalog = Catalog(
        schema_version=2,
        material_config_fingerprint="test",
        entry_order=[sid],
        entries={sid: entry},
    )
    evidence = {
        sid: PageEnrichEvidence(
            ok=False,
            error_kind="timeout",
            error_message="timed out",
            status_code=None,
        )
    }
    essay = Essay(
        position=1,
        title="A",
        url=sid,
        stable_id=sid,
        is_permalink=True,
        summary="prior good",
    )
    next_catalog = _apply_enrichment(catalog, [essay], now=T_LATER, page_evidence=evidence)
    page = next_catalog.entries[sid].page
    assert page.last_checked_at == T_LATER
    assert page.last_attempted_at == T_LATER
    assert page.last_response_at == T_LATER
    assert page.last_success_at == T0
    assert page.failure_count == 1
    assert page.last_error_kind == "timeout"
    assert page.next_retry_at is not None
    assert next_catalog.entries[sid].observed_updated_at == T0
    assert next_catalog.entries[sid].summary == "prior good"


def test_catalog_only_path_recover_true_divergent_feeds_publishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RV-C-001: catalog-only path publishes when recover rematerializes other feeds."""
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
    first_rss = (tmp_path / "feeds" / "rss.xml").read_bytes()

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

    poison = b"<rss>G1-POISON</rss>"
    g1 = seeded.model_copy(update={"last_generation_id": "gen-g1"})
    _write_pending_generation(
        tmp_path,
        g1,
        rss=poison,
        atom=poison,
        json_feed=poison,
        simple_rss=poison,
        simple_atom=poison,
        simple_json_feed=poison,
    )

    second = run_catalog_pipeline(settings, html=html, now=T_LATER)
    assert second.action == "updated"
    assert second.skipped is False
    assert (tmp_path / "feeds" / "rss.xml").read_bytes() != poison
    assert (tmp_path / "feeds" / "rss.xml").read_bytes() == first_rss
    on_disk = load_catalog(default_catalog_path(tmp_path))
    assert on_disk is not None
    for entry in on_disk.entries.values():
        assert entry.page.last_checked_at is not None
        assert entry.page.last_checked_at >= T_LATER - timedelta(days=1)
