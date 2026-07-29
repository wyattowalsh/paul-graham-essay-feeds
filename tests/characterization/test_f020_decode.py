"""F-020: unified HTML decoder must not use bare latin-1 fallback alone."""

from __future__ import annotations

from pathlib import Path

import pytest

from paul_graham_essay_feeds.http import decode_html

UPSTREAM = Path(__file__).resolve().parents[1] / "fixtures" / "upstream"


@pytest.mark.characterization
def test_decoder_exposes_selected_encoding_api() -> None:
    """Contract: decoder returns document + encoding evidence."""
    from paul_graham_essay_feeds import http as http_mod

    assert hasattr(http_mod, "decode_html_document")
    doc = http_mod.decode_html_document(b"hello")
    assert doc.text == "hello"
    assert doc.encoding == "utf-8"


@pytest.mark.characterization
def test_current_decode_html_accepts_any_bytes() -> None:
    """Characterization of *current* behavior: never raises on arbitrary bytes."""
    # Windows-1252 smart quote 0x93 — latin-1 maps to U+0093 (control), not U+201C.
    text = decode_html(b"\x93hello\x94")
    assert isinstance(text, str)
    assert len(text) == 7


@pytest.mark.characterization
def test_f020_fixture_encoding_corpus() -> None:
    """Encoding fixtures decode under policy (BOM + windows-1252 meta)."""
    from paul_graham_essay_feeds import http as http_mod

    bom = (UPSTREAM / "encoding-utf8-bom.html").read_bytes()
    bom_doc = http_mod.decode_html_document(bom)
    assert bom_doc.had_bom is True
    assert "\ufeff" not in bom_doc.text

    legacy = (UPSTREAM / "encoding-windows-1252.bin").read_bytes()
    legacy_doc = http_mod.decode_html_document(legacy)
    assert legacy_doc.encoding == "windows-1252"
    assert "\u201c" in legacy_doc.text
