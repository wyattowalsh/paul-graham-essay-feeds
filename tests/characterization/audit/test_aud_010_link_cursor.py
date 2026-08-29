"""AUD-010: dedicated link probes rotate via catalog.versions link_validation_cursor."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from paul_graham_essay_feeds.catalog import default_catalog_path, load_catalog
from paul_graham_essay_feeds.models import Essay
from paul_graham_essay_feeds.pipeline import run_catalog_pipeline
from paul_graham_essay_feeds.settings import Settings
from tests.html_samples import synthetic_index_html

T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)


def _settings(tmp_path: Path, **kwargs: object) -> Settings:
    data: dict[str, object] = {
        "repo_root": tmp_path,
        "min_items": 3,
        "enrich": False,
        "force": True,
        "quiet": True,
        "validate_links": True,
        "max_link_validations": 1,
    }
    data.update(kwargs)
    return Settings.model_validate(data)


def _capture_probes(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    probed: list[list[str]] = []

    def _validate(essays: list[Essay], **kwargs: object) -> object:
        probed.append([essay.stable_id for essay in essays])
        return MagicMock()

    monkeypatch.setattr("paul_graham_essay_feeds.pipeline.validate_essays_live", _validate)
    return probed


def test_aud_010_every_eligible_id_probed_before_repeat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Capped runs visit every eligible ID once before any second dedicated probe."""
    html = synthetic_index_html(essay_count=2)
    probed = _capture_probes(monkeypatch)
    settings = _settings(tmp_path)
    n_runs = 4
    for _ in range(n_runs):
        result = run_catalog_pipeline(settings, html=html, now=T0)
        assert result.action == "updated"

    catalog = load_catalog(default_catalog_path(tmp_path))
    assert catalog is not None
    eligible = list(catalog.entry_order)
    assert len(eligible) == n_runs
    assert catalog.versions.get("link_validation_cursor") == "0"

    flat = [sid for run in probed for sid in run]
    assert len(flat) == n_runs
    assert len(set(flat)) == n_runs
    assert set(flat) == set(eligible)
    assert all(len(run) == 1 for run in probed)


def test_aud_010_enrich_exclusion_rotates_remaining_eligible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When enrich GETs a URL, probes rotate across the remaining eligible IDs."""
    from paul_graham_essay_feeds.enrich import PageEnrichEvidence

    html = synthetic_index_html(essay_count=2)
    probed = _capture_probes(monkeypatch)
    enriched_ids: list[list[str]] = []

    def _enrich(essays: list[Essay], **kwargs: object) -> list[Essay]:
        enriched_ids.append([essay.stable_id for essay in essays])
        page_evidence_out = kwargs.get("page_evidence_out")
        out = [
            essay.model_copy(
                update={
                    "summary": f"Summary for {essay.title}",
                    "content_hash": "ab" * 32,
                }
            )
            for essay in essays
        ]
        if page_evidence_out is not None:
            for essay in out:
                page_evidence_out[essay.stable_id] = PageEnrichEvidence(  # type: ignore[index]
                    ok=True, status_code=200
                )
        return out

    monkeypatch.setattr("paul_graham_essay_feeds.pipeline.enrich_essays", _enrich)
    settings = _settings(
        tmp_path,
        enrich=True,
        max_page_fetches=1,
        max_link_validations=1,
    )
    n_eligible_remaining = 3
    for _ in range(n_eligible_remaining):
        run_catalog_pipeline(settings, html=html, now=T0)

    catalog = load_catalog(default_catalog_path(tmp_path))
    assert catalog is not None
    assert "link_validation_cursor" in catalog.versions

    assert len(probed) == n_eligible_remaining
    for run_probes, run_enrich in zip(probed, enriched_ids, strict=True):
        assert len(run_probes) == 1
        assert len(run_enrich) == 1
        assert set(run_probes).isdisjoint(run_enrich)

    remaining_probes = [sid for run in probed for sid in run]
    assert len(remaining_probes) == n_eligible_remaining
    assert len(set(remaining_probes)) == n_eligible_remaining
