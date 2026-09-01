"""AUD-001: skip-enrich still takes the writer lock, recovers, and verifies."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from paul_graham_essay_feeds.catalog import default_catalog_path, load_catalog
from paul_graham_essay_feeds.feeds import all_feed_paths
from paul_graham_essay_feeds.models import (
    MATERIALIZE_POINTER_SCHEMA_VERSION,
    MIN_ITEMS,
    FeedError,
    VerificationError,
)
from paul_graham_essay_feeds.pipeline import run_catalog_pipeline
from paul_graham_essay_feeds.publication import write_staging_generation
from paul_graham_essay_feeds.settings import Settings
from tests.html_samples import synthetic_index_html

T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)


def _settings(tmp_path: Path, **kwargs: object) -> Settings:
    data: dict[str, object] = {
        "repo_root": tmp_path,
        "min_items": MIN_ITEMS,
        "enrich": False,
        "force": False,
        "quiet": True,
        "validate_links": False,
    }
    data.update(kwargs)
    return Settings.model_validate(data)


def test_aud_001_seeded_pointer_recovers_on_unchanged_update(tmp_path: Path) -> None:
    """Valid materialize.json + matching staged gen: recover runs, pointer cleared."""
    html = synthetic_index_html()
    settings = _settings(tmp_path)
    first = run_catalog_pipeline(settings, html=html, now=T0)
    assert first.action == "updated"

    catalog = load_catalog(default_catalog_path(tmp_path))
    assert catalog is not None
    paths = all_feed_paths(tmp_path)
    gen_id = write_staging_generation(
        tmp_path,
        catalog=catalog,
        rss=paths["rss"].read_bytes(),
        atom=paths["atom"].read_bytes(),
        json_feed=paths["json"].read_bytes(),
        simple_rss=paths["rss_simple"].read_bytes(),
        simple_atom=paths["atom_simple"].read_bytes(),
        simple_json_feed=paths["json_simple"].read_bytes(),
    )
    pointer = tmp_path / ".cache" / "materialize.json"
    pointer.write_text(
        json.dumps(
            {
                "schema_version": MATERIALIZE_POINTER_SCHEMA_VERSION,
                "gen_id": gen_id,
                "phase": "materializing",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    assert pointer.is_file()

    with pytest.raises(FeedError, match="state revision"):
        run_catalog_pipeline(settings, html=html, now=T0)
    assert not pointer.exists()
    recovered = load_catalog(default_catalog_path(tmp_path))
    assert recovered is not None
    assert recovered.last_generation_id == gen_id

    third = run_catalog_pipeline(settings, html=html, now=T0)
    assert third.action in {"unchanged", "state_changed"}


def test_aud_001_corrupt_feed_does_not_report_unchanged(tmp_path: Path) -> None:
    """All seven paths present but one feed corrupt → verify fails, never unchanged."""
    html = synthetic_index_html()
    settings = _settings(tmp_path)
    first = run_catalog_pipeline(settings, html=html, now=T0)
    assert first.action == "updated"

    rss = tmp_path / "feeds" / "rss.xml"
    assert rss.is_file()
    rss.write_bytes(b"not-valid-rss")
    for name in (
        "atom.xml",
        "feed.json",
        "rss.simple.xml",
        "atom.simple.xml",
        "feed.simple.json",
    ):
        assert (tmp_path / "feeds" / name).is_file()
    assert default_catalog_path(tmp_path).is_file()

    with pytest.raises((FeedError, VerificationError)):
        run_catalog_pipeline(settings, html=html, now=T0)
