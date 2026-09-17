"""T-PIPE / RV-C-003 + RV-C-004: 304 index_hash and catalog permalink rebuild."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from paul_graham_essay_feeds.catalog import default_catalog_path, load_catalog, save_catalog
from paul_graham_essay_feeds.http import IndexFetchResult
from paul_graham_essay_feeds.models import (
    MIN_ITEMS,
    Catalog,
    CatalogEntry,
    content_sha256,
    make_stable_id,
)
from paul_graham_essay_feeds.pipeline import _essays_from_catalog, run_catalog_pipeline
from paul_graham_essay_feeds.settings import Settings
from tests.html_samples import synthetic_index_html

_PIPELINE = Path(__file__).resolve().parents[3] / "src" / "paul_graham_essay_feeds" / "pipeline.py"
_T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)


@pytest.mark.characterization
def test_pipeline_source_never_hashes_304_sentinel() -> None:
    """RV-C-003: do not fabricate index_hash from content_sha256('304-not-modified')."""
    src = _PIPELINE.read_text(encoding="utf-8")
    assert "304-not-modified" not in src


@pytest.mark.characterization
def test_essays_from_catalog_permalink_matches_feeds_guid_rule() -> None:
    """RV-C-004: is_permalink is stable_id == url (same rule as RSS guid)."""
    essay_url = "https://paulgraham.com/earn.html"
    chapter_url = "https://sep.turbifycdn.com/ty/cdn/paulgraham/acl2.txt"
    chapter_id, chapter_permalink = make_stable_id(chapter_url)
    assert chapter_permalink is False
    catalog = Catalog(
        schema_version=2,
        material_config_fingerprint="test",
        entry_order=[essay_url, chapter_id],
        entries={
            essay_url: CatalogEntry(
                stable_id=essay_url,
                url=essay_url,
                title="How to Make Wealth",
                position=0,
            ),
            chapter_id: CatalogEntry(
                stable_id=chapter_id,
                url=chapter_url,
                title="Chapter 2 of Ansi Common Lisp",
                position=1,
            ),
        },
    )
    essays = _essays_from_catalog(catalog)
    by_id = {essay.stable_id: essay for essay in essays}
    assert by_id[essay_url].is_permalink is True
    assert by_id[chapter_id].is_permalink is False
    assert by_id[chapter_id].is_permalink is (chapter_id == chapter_url)


@pytest.mark.characterization
def test_index_304_omits_fabricated_json_index_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RV-C-003: published JSON must omit index_hash when 304 has no prior digest."""
    settings = Settings.model_validate(
        {
            "repo_root": tmp_path,
            "min_items": MIN_ITEMS,
            "enrich": False,
            "force": False,
            "quiet": True,
            "validate_links": False,
            "max_page_fetches": None,
            "max_link_validations": None,
        }
    )
    first = run_catalog_pipeline(settings, html=synthetic_index_html(), now=_T0)
    assert first.index_hash is not None
    seeded = load_catalog(default_catalog_path(tmp_path))
    assert seeded is not None
    save_catalog(
        default_catalog_path(tmp_path),
        seeded.model_copy(
            update={
                "index": seeded.index.model_copy(
                    update={"raw_sha256": None, "decoded_sha256": None}
                )
            }
        ),
    )
    shutil.rmtree(tmp_path / "feeds")
    monkeypatch.setattr(
        "paul_graham_essay_feeds.pipeline.fetch_index",
        MagicMock(
            return_value=IndexFetchResult(
                html=None,
                not_modified=True,
                etag='"idx-c003"',
                status_code=304,
            )
        ),
    )
    second = run_catalog_pipeline(settings, now=_T0)
    sentinel = content_sha256("304-not-modified")
    assert second.index_hash is None
    for name in ("feed.json", "feed.simple.json"):
        text = (tmp_path / "feeds" / name).read_text(encoding="utf-8")
        meta = json.loads(text)["_pg_essay_feeds"]
        assert "index_hash" not in meta
        assert sentinel not in text
