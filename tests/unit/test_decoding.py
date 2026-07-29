"""Unit tests for http decoding (decode_html_document)."""

from __future__ import annotations

from pathlib import Path

from paul_graham_essay_feeds.http import EncodingSource, decode_html, decode_html_document

UPSTREAM = Path(__file__).resolve().parents[1] / "fixtures" / "upstream"


def test_utf8_strict() -> None:
    doc = decode_html_document("café".encode())
    assert doc.text == "café"
    assert doc.encoding == "utf-8"
    assert doc.source is EncodingSource.UTF8_STRICT


def test_bom_utf8() -> None:
    doc = decode_html_document(b"\xef\xbb\xbfhello")
    assert doc.text == "hello"
    assert doc.source is EncodingSource.BOM
    assert doc.had_bom is True


def test_fixture_encoding_utf8_bom() -> None:
    raw = (UPSTREAM / "encoding-utf8-bom.html").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    doc = decode_html_document(raw)
    assert doc.had_bom is True
    assert doc.source is EncodingSource.BOM
    assert "\ufeff" not in doc.text


def test_fixture_encoding_windows_1252() -> None:
    raw = (UPSTREAM / "encoding-windows-1252.bin").read_bytes()
    doc = decode_html_document(raw)
    assert doc.encoding == "windows-1252"
    assert doc.source is EncodingSource.META
    assert "\u201c" in doc.text


def test_transport_charset_windows_1252() -> None:
    # 0x93 is smart quote in windows-1252 → U+201C
    raw = b"\x93hi\x94"
    doc = decode_html_document(raw, transport_charset="windows-1252")
    assert doc.source is EncodingSource.TRANSPORT
    assert doc.encoding == "windows-1252"
    assert "\u201c" in doc.text


def test_meta_charset_prescan() -> None:
    raw = b'<meta charset="windows-1252"><title>\x93x\x94</title>'
    doc = decode_html_document(raw)
    assert doc.source is EncodingSource.META
    assert doc.encoding == "windows-1252"


def test_fallback_windows_1252() -> None:
    raw = b"\xff\xfe not utf8 really \x93"
    # invalid utf-8 → fallback
    doc = decode_html_document(raw)
    assert doc.source is EncodingSource.WINDOWS_1252_FALLBACK
    assert doc.encoding == "windows-1252"


def test_decode_html_compat_str() -> None:
    assert isinstance(decode_html(b"abc"), str)


def test_invalid_transport_charset_falls_through() -> None:
    doc = decode_html_document(b"plain-ascii", transport_charset="not-a-codec-zzzz")
    assert doc.text == "plain-ascii"
    assert doc.encoding == "utf-8"


def test_meta_http_equiv_charset() -> None:
    raw = b'<meta http-equiv="Content-Type" content="text/html; charset=windows-1252">\x93x\x94'
    doc = decode_html_document(raw)
    assert doc.source is EncodingSource.META
    assert "\u201c" in doc.text


def test_unknown_meta_label_ignored() -> None:
    # Unknown meta labels are ignored; UTF-8 strict still decodes the raw bytes.
    raw = b'<meta charset="x-unknown-99">hello'
    doc = decode_html_document(raw)
    assert "hello" in doc.text
    assert doc.source is EncodingSource.UTF8_STRICT
