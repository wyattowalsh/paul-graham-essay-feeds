"""P0 upstream fixture corpus: discovery matrix + encoding samples."""

from __future__ import annotations

from pathlib import Path

import pytest

from paul_graham_essay_feeds.discover import ExtractionStrategy, discover_essays
from paul_graham_essay_feeds.http import EncodingSource, decode_html_document
from paul_graham_essay_feeds.models import FeedError

UPSTREAM = Path(__file__).resolve().parents[1] / "fixtures" / "upstream"

INDEX_FIXTURES = (
    "index-marker-basic.html",
    "index-marker-leak.html",
    "index-sparse-fallback.html",
    "index-duplicate-anchors.html",
)


@pytest.mark.characterization
@pytest.mark.parametrize("name", INDEX_FIXTURES)
def test_index_fixture_files_exist(name: str) -> None:
    path = UPSTREAM / name
    assert path.is_file()
    assert path.stat().st_size > 0


@pytest.mark.characterization
def test_fixture_marker_basic_binds_marker_to_anchor() -> None:
    html = (UPSTREAM / "index-marker-basic.html").read_text(encoding="utf-8")
    essays, report = discover_essays(html, min_items=8, allow_fallback=False)
    assert len(essays) >= 8
    assert essays[0].title == "Essay 0"
    assert essays[0].url.endswith("/essay-0.html")
    assert report.strategy is ExtractionStrategy.MARKER
    assert report.fallback_used is False
    assert report.marked_count >= 8


@pytest.mark.characterization
def test_fixture_marker_leak_no_cross_row_nav() -> None:
    html = (UPSTREAM / "index-marker-leak.html").read_text(encoding="utf-8")
    essays, report = discover_essays(html, min_items=7, allow_fallback=False)
    assert all(not e.url.endswith("/articles.html") for e in essays)
    assert any(e.url.endswith("/keep-0.html") for e in essays)
    assert report.strategy is ExtractionStrategy.MARKER
    assert report.fallback_used is False


@pytest.mark.characterization
def test_fixture_sparse_fail_closed_and_fallback() -> None:
    html = (UPSTREAM / "index-sparse-fallback.html").read_text(encoding="utf-8")
    with pytest.raises(FeedError, match="fallback disabled"):
        discover_essays(html, min_items=8, allow_fallback=False)
    essays, report = discover_essays(html, min_items=8, allow_fallback=True)
    assert len(essays) >= 8
    assert report.fallback_used is True
    assert report.strategy is ExtractionStrategy.FALLBACK
    assert report.marked_count == 0


@pytest.mark.characterization
def test_fixture_duplicate_anchors_first_wins() -> None:
    html = (UPSTREAM / "index-duplicate-anchors.html").read_text(encoding="utf-8")
    essays, report = discover_essays(html, min_items=8, allow_fallback=False)
    dups = [e for e in essays if e.url.endswith("/dup.html")]
    assert len(dups) == 1
    assert dups[0].title == "First Title"
    assert any(d.endswith("/dup.html") for d in report.duplicates)


@pytest.mark.characterization
def test_fixture_encoding_utf8_bom() -> None:
    raw = (UPSTREAM / "encoding-utf8-bom.html").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    doc = decode_html_document(raw)
    assert doc.had_bom is True
    assert doc.source is EncodingSource.BOM
    assert "café" in doc.text or "BOM" in doc.text
    assert "\ufeff" not in doc.text


@pytest.mark.characterization
def test_fixture_encoding_windows_1252() -> None:
    raw = (UPSTREAM / "encoding-windows-1252.bin").read_bytes()
    doc = decode_html_document(raw)
    assert doc.encoding == "windows-1252"
    assert "\u201c" in doc.text  # smart quote from 0x93
    assert doc.source is EncodingSource.META
