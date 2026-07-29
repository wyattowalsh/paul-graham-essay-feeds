"""Wave 4: lightweight fault / property smoke (offline)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from paul_graham_essay_feeds.catalog import default_catalog_path, load_catalog
from paul_graham_essay_feeds.models import MIN_ITEMS, FeedError
from paul_graham_essay_feeds.pipeline import material_catalog_digest, run_catalog_pipeline
from paul_graham_essay_feeds.settings import Settings
from paul_graham_essay_feeds.verify import assert_verified
from tests.html_samples import synthetic_index_html

T0 = datetime(2024, 1, 1, tzinfo=UTC)


def test_corrupt_catalog_fail_closed(tmp_path: Path) -> None:
    path = default_catalog_path(tmp_path)
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(FeedError, match="Corrupt catalog"):
        load_catalog(path)


def test_verify_rejects_empty_rss() -> None:
    with pytest.raises(FeedError):
        assert_verified(rss=b"", atom=b"<feed/>", json_feed=b"{}", min_items=1)


def test_pipeline_deterministic_material_digest(tmp_path: Path) -> None:
    html = synthetic_index_html()
    settings = Settings.model_validate(
        {
            "repo_root": tmp_path,
            "min_items": MIN_ITEMS,
            "enrich": False,
            "quiet": True,
            "force": True,
        }
    )
    a = run_catalog_pipeline(settings, html=html, now=T0)
    b = run_catalog_pipeline(settings, html=html, now=T0)
    assert a.action == "updated"
    assert b.action == "updated"
    assert material_catalog_digest(a.catalog) == material_catalog_digest(b.catalog)
    assert default_catalog_path(tmp_path).is_file()
    assert not (tmp_path / "state" / "current.json").exists()
