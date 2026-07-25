"""F-020: unified HTML decoder must not use bare latin-1 fallback alone."""

from __future__ import annotations

import pytest

from paul_graham_essay_feeds.fetch import decode_html


@pytest.mark.characterization
def test_decoder_exposes_selected_encoding_api() -> None:
    """Contract: decoder returns document + encoding evidence."""
    from paul_graham_essay_feeds import decoding

    assert hasattr(decoding, "decode_html_document")
    doc = decoding.decode_html_document(b"hello")
    assert doc.text == "hello"
    assert doc.encoding == "utf-8"


@pytest.mark.characterization
def test_current_decode_html_accepts_any_bytes() -> None:
    """Characterization of *current* behavior: never raises on arbitrary bytes."""
    # Windows-1252 smart quote 0x93 — latin-1 maps to U+0093 (control), not U+201C.
    text = decode_html(b"\x93hello\x94")
    assert isinstance(text, str)
    assert len(text) == 7
