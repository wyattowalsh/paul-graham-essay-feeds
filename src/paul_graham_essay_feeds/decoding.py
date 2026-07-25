"""Deterministic HTML-oriented byte decoding (ADR-004)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

# Windows-1252 is preferred over ISO-8859-1 for legacy HTML punctuation (C1 range).
_META_CHARSET = re.compile(
    rb"(?is)<meta[^>]+charset\s*=\s*[\"']?\s*([a-zA-Z0-9_\-]+)",
)
_META_CONTENT_TYPE = re.compile(
    rb"(?is)<meta[^>]+http-equiv\s*=\s*[\"']?content-type[\"']?[^>]+content\s*=\s*[\"'][^\"']*charset\s*=\s*([a-zA-Z0-9_\-]+)",
)
_ALLOWED_ENCODINGS = frozenset(
    {
        "utf-8",
        "utf8",
        "windows-1252",
        "cp1252",
        "iso-8859-1",
        "latin-1",
        "latin1",
    }
)


class EncodingSource(StrEnum):
    BOM = "bom"
    TRANSPORT = "transport"
    META = "meta"
    UTF8_STRICT = "utf8_strict"
    WINDOWS_1252_FALLBACK = "windows_1252_fallback"


@dataclass(frozen=True, slots=True)
class DecodedDocument:
    """Decoded HTML text plus encoding selection evidence."""

    text: str
    encoding: str
    source: EncodingSource
    had_bom: bool = False
    replacement_count: int = 0


def _normalize_encoding_label(label: str) -> str | None:
    key = label.strip().lower().replace("_", "-")
    if key not in _ALLOWED_ENCODINGS and key not in {"utf-8", "utf8"}:
        # Allow common aliases only from the allowlist for safety.
        return None
    if key in {"utf8", "utf-8"}:
        return "utf-8"
    if key in {"cp1252", "windows-1252"}:
        return "windows-1252"
    if key in {"latin-1", "latin1", "iso-8859-1"}:
        return "windows-1252"  # treat latin-1 declaration as cp1252 for HTML
    return key


def _prescan_meta_charset(raw: bytes, limit: int = 4096) -> str | None:
    head = raw[:limit]
    for pattern in (_META_CHARSET, _META_CONTENT_TYPE):
        match = pattern.search(head)
        if match:
            return _normalize_encoding_label(match.group(1).decode("ascii", errors="ignore"))
    return None


def decode_html_document(
    body: bytes,
    *,
    transport_charset: str | None = None,
) -> DecodedDocument:
    """Decode HTML bytes using the ADR-004 priority chain.

    1. BOM
    2. Valid transport charset
    3. Early in-document meta charset
    4. Strict UTF-8
    5. Windows-1252 fallback
    """
    had_bom = body.startswith(b"\xef\xbb\xbf")
    if had_bom:
        text = body.decode("utf-8-sig")
        return DecodedDocument(
            text=text,
            encoding="utf-8",
            source=EncodingSource.BOM,
            had_bom=True,
            replacement_count=text.count("\ufffd"),
        )

    if transport_charset:
        enc = _normalize_encoding_label(transport_charset)
        if enc:
            try:
                text = body.decode(enc)
                return DecodedDocument(
                    text=text,
                    encoding=enc,
                    source=EncodingSource.TRANSPORT,
                    replacement_count=text.count("\ufffd"),
                )
            except LookupError:
                pass
            except UnicodeDecodeError:
                pass

    meta = _prescan_meta_charset(body)
    if meta:
        try:
            text = body.decode(meta)
            return DecodedDocument(
                text=text,
                encoding=meta,
                source=EncodingSource.META,
                replacement_count=text.count("\ufffd"),
            )
        except (LookupError, UnicodeDecodeError):
            pass

    try:
        text = body.decode("utf-8")
        return DecodedDocument(
            text=text,
            encoding="utf-8",
            source=EncodingSource.UTF8_STRICT,
            replacement_count=0,
        )
    except UnicodeDecodeError:
        text = body.decode("windows-1252", errors="replace")
        return DecodedDocument(
            text=text,
            encoding="windows-1252",
            source=EncodingSource.WINDOWS_1252_FALLBACK,
            replacement_count=text.count("\ufffd"),
        )


def decode_html(body: bytes, *, transport_charset: str | None = None) -> str:
    """Back-compat helper returning text only."""
    return decode_html_document(body, transport_charset=transport_charset).text
