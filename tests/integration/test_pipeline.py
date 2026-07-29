"""Integration: catalog pipeline publish parity and unchanged gate."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from paul_graham_essay_feeds.catalog import default_catalog_path
from paul_graham_essay_feeds.discovery import discover_essays
from paul_graham_essay_feeds.models import MIN_ITEMS
from paul_graham_essay_feeds.pipeline import run_catalog_pipeline
from paul_graham_essay_feeds.settings import Settings
from tests.html_samples import synthetic_index_html

pytestmark = pytest.mark.integration

T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)


def _settings(root: Path, **kwargs: object) -> Settings:
    data: dict[str, object] = {
        "repo_root": root,
        "min_items": MIN_ITEMS,
        "enrich": False,
        "force": False,
        "quiet": True,
        "validate_links": False,
    }
    data.update(kwargs)
    return Settings.model_validate(data)


def test_pipeline_offline(repo_root: Path, sample_html: str) -> None:
    """Offline catalog pipeline still publishes feed projections."""
    settings = _settings(repo_root)
    result = run_catalog_pipeline(settings, html=sample_html, now=T0)
    assert result.action == "updated"

    essays, _report = discover_essays(sample_html, min_items=MIN_ITEMS)
    rss = (repo_root / "feeds" / "rss.xml").read_text(encoding="utf-8")
    atom = (repo_root / "feeds" / "atom.xml").read_text(encoding="utf-8")
    data = json.loads((repo_root / "feeds" / "feed.json").read_text(encoding="utf-8"))
    n = len(essays)
    assert n >= MIN_ITEMS
    assert rss.count("<item>") == n
    assert atom.count("<entry>") == n
    assert len(data["items"]) == n
    assert data["items"][0]["url"] == essays[0].url
    assert data["_pg_essay_feeds"]["index_hash"] == result.index_hash
    assert not (repo_root / "data" / "essays.json").exists()
    assert default_catalog_path(repo_root).is_file()
    assert not (repo_root / "state" / "current.json").exists()
    assert not (repo_root / "state" / "generations").exists()


def test_catalog_publish_root_catalog_and_feeds(tmp_path: Path) -> None:
    """Happy path: root catalog.json + feeds/ after verify."""
    html = synthetic_index_html()
    settings = _settings(tmp_path)
    result = run_catalog_pipeline(settings, html=html, now=T0)

    assert result.action == "updated"
    catalog = json.loads(default_catalog_path(tmp_path).read_text(encoding="utf-8"))
    assert len(catalog["entry_order"]) >= MIN_ITEMS
    for name in ("rss.xml", "atom.xml", "feed.json"):
        assert (tmp_path / "feeds" / name).is_file()
    assert not (tmp_path / "state" / "current.json").exists()
    assert not (tmp_path / "state" / "generations").exists()


def test_catalog_second_pass_unchanged_no_mtime_churn(tmp_path: Path) -> None:
    """Re-run with identical index → unchanged and zero tracked mtime churn."""
    html = synthetic_index_html()
    settings = _settings(tmp_path)
    first = run_catalog_pipeline(settings, html=html, now=T0)
    assert first.action == "updated"

    tracked = [
        default_catalog_path(tmp_path),
        tmp_path / "feeds" / "rss.xml",
        tmp_path / "feeds" / "atom.xml",
        tmp_path / "feeds" / "feed.json",
    ]
    snapshots = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in tracked}

    second = run_catalog_pipeline(settings, html=html, now=T0)
    assert second.action == "unchanged"
    assert second.skipped is True

    for path, (blob, mtime_ns) in snapshots.items():
        assert path.read_bytes() == blob
        assert path.stat().st_mtime_ns == mtime_ns
