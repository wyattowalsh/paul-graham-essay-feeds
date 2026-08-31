"""C-01 / L-14: catalog-only refreshes must surface state_changed (not unchanged).

Pinned defect: post-enrich material-noop saved catalog.json but returned
action=unchanged, so update-feeds.yml never uploaded/committed catalog clocks.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from paul_graham_essay_feeds.catalog import default_catalog_path, load_catalog, save_catalog
from paul_graham_essay_feeds.models import MIN_ITEMS, Essay
from paul_graham_essay_feeds.pipeline import PipelineAction, run_catalog_pipeline
from paul_graham_essay_feeds.settings import Settings
from tests.html_samples import synthetic_index_html

T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
T_LATER = T0 + timedelta(days=40)


def _settings(tmp_path: Path, **kwargs: object) -> Settings:
    data: dict[str, object] = {
        "repo_root": tmp_path,
        "min_items": MIN_ITEMS,
        "enrich": True,
        "force": False,
        "quiet": True,
        "validate_links": False,
        "stale_after_days": 30,
        # Production defaults cap at 40 (PGF-2026-014); this test needs a full enrich.
        "max_page_fetches": None,
        "max_link_validations": None,
    }
    data.update(kwargs)
    return Settings.model_validate(data)


def _stable_enrich(essays: list[Essay], **kwargs: object) -> list[Essay]:
    from paul_graham_essay_feeds.enrich import PageEnrichEvidence

    out = [
        essay.model_copy(
            update={
                "summary": f"Summary for {essay.title}",
                "content_hash": f"hash-{essay.stable_id}",
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


def test_c01_catalog_only_refresh_reports_state_changed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """STALE enrich with identical material → state_changed + catalog.json only."""
    html = synthetic_index_html()
    enrich = MagicMock(side_effect=_stable_enrich)
    monkeypatch.setattr("paul_graham_essay_feeds.pipeline.enrich_essays", enrich)

    settings = _settings(tmp_path)
    first = run_catalog_pipeline(settings, html=html, now=T0)
    assert first.action == PipelineAction.MATERIAL_CHANGED.value
    assert "catalog.json" in first.changed_paths
    assert any(p.startswith("feeds/") for p in first.changed_paths)

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

    feed_paths = [
        tmp_path / "feeds" / name
        for name in (
            "rss.xml",
            "atom.xml",
            "feed.json",
            "rss.simple.xml",
            "atom.simple.xml",
            "feed.simple.json",
        )
    ]
    feed_bytes = {path: path.read_bytes() for path in feed_paths}

    second = run_catalog_pipeline(settings, html=html, now=T_LATER)
    assert second.action == PipelineAction.STATE_CHANGED.value
    assert second.changed_paths == ("catalog.json",)
    for path, prior in feed_bytes.items():
        assert path.read_bytes() == prior

    third = run_catalog_pipeline(settings, html=html, now=T_LATER)
    assert third.action == PipelineAction.NO_CHANGE.value
    assert third.changed_paths == ()
    assert third.skipped is True


def test_c01_workflow_publishes_state_changed() -> None:
    """Scheduled workflow must upload/publish for state_changed and updated."""
    yml = Path(".github/workflows/update-feeds.yml").read_text(encoding="utf-8")
    assert "state_changed" in yml
    assert "updated" in yml
    # Both outcomes must appear in the publish gate (not only updated).
    assert "steps.update.outputs.action == 'state_changed'" in yml
    assert "needs.update.outputs.action == 'state_changed'" in yml
