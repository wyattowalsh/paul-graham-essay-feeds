"""Deep in-memory and on-disk feed bundle verification (F-015, F-003).

Prefer returning :class:`VerificationReport` for testability. Use
:func:`raise_on_failure` (or :func:`assert_verified`) when a hard fail is needed.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

from paul_graham_essay_feeds.models import (
    ATOM_NS,
    FEED_SUMMARY_CHARS,
    FeedError,
    VerificationError,
)

# Public violation codes (stable for tests and callers).
MISSING_FILE: Final = "MISSING_FILE"
UNPARSEABLE_XML: Final = "UNPARSEABLE_XML"
UNPARSEABLE_JSON: Final = "UNPARSEABLE_JSON"
COUNT_MISMATCH: Final = "COUNT_MISMATCH"
BELOW_MIN_ITEMS: Final = "BELOW_MIN_ITEMS"
DUPLICATE_ID: Final = "DUPLICATE_ID"
EMPTY_ID: Final = "EMPTY_ID"
EMPTY_TITLE: Final = "EMPTY_TITLE"
EMPTY_URL: Final = "EMPTY_URL"
EMPTY_SUMMARY: Final = "EMPTY_SUMMARY"
CONTENT_TEXT_MISMATCH: Final = "CONTENT_TEXT_MISMATCH"
SUMMARY_LENGTH: Final = "SUMMARY_LENGTH"
UNICODE_REPLACEMENT: Final = "UNICODE_REPLACEMENT"
ID_ORDER_MISMATCH: Final = "ID_ORDER_MISMATCH"
ARTIFACT_TOO_LARGE: Final = "ARTIFACT_TOO_LARGE"
_MAX_ARTIFACT_BYTES: Final = 20 * 1024 * 1024

_REPLACEMENT = "\ufffd"

_FEED_NAMES = {
    "rss": "rss.xml",
    "atom": "atom.xml",
    "json": "feed.json",
}


@dataclass(frozen=True, slots=True)
class VerificationViolation:
    """One discrete verification failure with a stable machine-readable code."""

    code: str
    message: str
    path: str | None = None
    index: int | None = None


@dataclass(frozen=True, slots=True)
class VerificationReport:
    """Aggregate result of deep feed verification."""

    ok: bool
    violations: list[VerificationViolation]


@dataclass(frozen=True, slots=True)
class _ItemView:
    """Normalized per-item fields extracted from one feed format."""

    item_id: str
    title: str
    url: str
    summary: str
    content_text: str | None = None


def _feed_paths(
    root: Path,
    *,
    relative_dir: str = "feeds",
) -> dict[str, Path]:
    feeds_dir = root / relative_dir
    return {
        "rss": feeds_dir / "rss.xml",
        "atom": feeds_dir / "atom.xml",
        "json": feeds_dir / "feed.json",
    }


def _text(el: ET.Element | None) -> str:
    if el is None or el.text is None:
        return ""
    return el.text


def _parse_rss_items(raw: bytes) -> list[_ItemView] | VerificationViolation:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        return VerificationViolation(
            code=UNPARSEABLE_XML,
            message=f"rss.xml is not valid XML: {exc}",
            path="feeds/rss.xml",
        )
    items: list[_ItemView] = []
    for item in root.findall(".//item"):
        items.append(
            _ItemView(
                item_id=_text(item.find("guid")),
                title=_text(item.find("title")),
                url=_text(item.find("link")),
                summary=_text(item.find("description")),
            )
        )
    return items


def _atom_link_href(entry: ET.Element) -> str:
    for link in entry.findall(f"{{{ATOM_NS}}}link"):
        rel = link.attrib.get("rel", "alternate")
        href = link.attrib.get("href", "")
        if rel == "alternate" and href:
            return href
    for link in entry.findall(f"{{{ATOM_NS}}}link"):
        href = link.attrib.get("href", "")
        if href:
            return href
    return ""


def _parse_atom_items(raw: bytes) -> list[_ItemView] | VerificationViolation:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        return VerificationViolation(
            code=UNPARSEABLE_XML,
            message=f"atom.xml is not valid XML: {exc}",
            path="feeds/atom.xml",
        )
    items: list[_ItemView] = []
    for entry in root.findall(f".//{{{ATOM_NS}}}entry"):
        items.append(
            _ItemView(
                item_id=_text(entry.find(f"{{{ATOM_NS}}}id")),
                title=_text(entry.find(f"{{{ATOM_NS}}}title")),
                url=_atom_link_href(entry),
                summary=_text(entry.find(f"{{{ATOM_NS}}}summary")),
            )
        )
    return items


def _parse_json_items(raw: bytes) -> list[_ItemView] | VerificationViolation:
    try:
        text = raw.decode("utf-8")
        payload = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return VerificationViolation(
            code=UNPARSEABLE_JSON,
            message=f"feed.json is not valid JSON: {exc}",
            path="feeds/feed.json",
        )
    if not isinstance(payload, dict):
        return VerificationViolation(
            code=UNPARSEABLE_JSON,
            message="feed.json root must be an object",
            path="feeds/feed.json",
        )
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        return VerificationViolation(
            code=UNPARSEABLE_JSON,
            message="feed.json missing items array",
            path="feeds/feed.json",
        )
    items: list[_ItemView] = []
    for i, raw_item in enumerate(raw_items):
        if not isinstance(raw_item, dict):
            return VerificationViolation(
                code=UNPARSEABLE_JSON,
                message=f"feed.json items[{i}] must be an object",
                path="feeds/feed.json",
                index=i,
            )
        item = cast(dict[str, object], raw_item)
        content_text = item.get("content_text")
        summary = item.get("summary")
        item_id = item.get("id")
        title = item.get("title")
        url = item.get("url")
        items.append(
            _ItemView(
                item_id=str(item_id) if item_id is not None else "",
                title=str(title) if title is not None else "",
                url=str(url) if url is not None else "",
                summary=summary if isinstance(summary, str) else "",
                content_text=content_text if isinstance(content_text, str) else None,
            )
        )
    return items


def _check_duplicates(
    items: list[_ItemView],
    *,
    path: str,
    format_label: str,
) -> list[VerificationViolation]:
    seen: dict[str, int] = {}
    out: list[VerificationViolation] = []
    for i, item in enumerate(items):
        if not item.item_id:
            continue
        if item.item_id in seen:
            out.append(
                VerificationViolation(
                    code=DUPLICATE_ID,
                    message=(
                        f"{format_label} duplicate id {item.item_id!r} "
                        f"at indices {seen[item.item_id]} and {i}"
                    ),
                    path=path,
                    index=i,
                )
            )
        else:
            seen[item.item_id] = i
    return out


def _check_item_fields(
    items: list[_ItemView],
    *,
    path: str,
    format_label: str,
    check_content_text: bool,
) -> list[VerificationViolation]:
    out: list[VerificationViolation] = []
    for i, item in enumerate(items):
        if not item.item_id.strip():
            out.append(
                VerificationViolation(
                    code=EMPTY_ID,
                    message=f"{format_label} items[{i}] has empty id",
                    path=path,
                    index=i,
                )
            )
        if not item.title.strip():
            out.append(
                VerificationViolation(
                    code=EMPTY_TITLE,
                    message=f"{format_label} items[{i}] has empty title",
                    path=path,
                    index=i,
                )
            )
        if not item.url.strip():
            out.append(
                VerificationViolation(
                    code=EMPTY_URL,
                    message=f"{format_label} items[{i}] has empty url",
                    path=path,
                    index=i,
                )
            )
        summary = item.summary
        if not summary.strip():
            out.append(
                VerificationViolation(
                    code=EMPTY_SUMMARY,
                    message=f"{format_label} items[{i}] has empty summary",
                    path=path,
                    index=i,
                )
            )
        else:
            n = len(summary)
            if not (1 <= n <= FEED_SUMMARY_CHARS):
                out.append(
                    VerificationViolation(
                        code=SUMMARY_LENGTH,
                        message=(
                            f"{format_label} items[{i}]: summary length {n} "
                            f"not in [1, {FEED_SUMMARY_CHARS}]"
                        ),
                        path=path,
                        index=i,
                    )
                )

        for field_name, value in (("title", item.title), ("summary", summary)):
            if _REPLACEMENT in value:
                out.append(
                    VerificationViolation(
                        code=UNICODE_REPLACEMENT,
                        message=(f"{format_label} items[{i}] {field_name} contains U+FFFD"),
                        path=path,
                        index=i,
                    )
                )

        if check_content_text:
            content_text = item.content_text
            if content_text is None:
                out.append(
                    VerificationViolation(
                        code=CONTENT_TEXT_MISMATCH,
                        message=(f"{format_label} items[{i}] requires string content_text"),
                        path=path,
                        index=i,
                    )
                )
            else:
                if content_text != summary:
                    out.append(
                        VerificationViolation(
                            code=CONTENT_TEXT_MISMATCH,
                            message=(f"{format_label} items[{i}]: content_text must equal summary"),
                            path=path,
                            index=i,
                        )
                    )
                n = len(content_text)
                if content_text.strip() and not (1 <= n <= FEED_SUMMARY_CHARS):
                    out.append(
                        VerificationViolation(
                            code=SUMMARY_LENGTH,
                            message=(
                                f"{format_label} items[{i}]: content_text length {n} "
                                f"not in [1, {FEED_SUMMARY_CHARS}]"
                            ),
                            path=path,
                            index=i,
                        )
                    )
                if _REPLACEMENT in content_text:
                    out.append(
                        VerificationViolation(
                            code=UNICODE_REPLACEMENT,
                            message=(f"{format_label} items[{i}] content_text contains U+FFFD"),
                            path=path,
                            index=i,
                        )
                    )
    return out


def verify_feed_bytes(
    *,
    rss: bytes,
    atom: bytes,
    json_feed: bytes,
    min_items: int,
) -> VerificationReport:
    """Deep-verify an in-memory RSS/Atom/JSON feed triple.

    Checks parseability, size caps, count parity, min-items floor, duplicate
    ids, empty id/title/url/summary, JSON ``content_text == summary``, summary
    length bounds, U+FFFD integrity, and ordered id parity across formats.
    """
    violations: list[VerificationViolation] = []
    for label, blob, path in (
        ("rss", rss, "feeds/rss.xml"),
        ("atom", atom, "feeds/atom.xml"),
        ("json", json_feed, "feeds/feed.json"),
    ):
        if len(blob) > _MAX_ARTIFACT_BYTES:
            violations.append(
                VerificationViolation(
                    code=ARTIFACT_TOO_LARGE,
                    message=(f"{label} artifact is {len(blob)} bytes (max {_MAX_ARTIFACT_BYTES})"),
                    path=path,
                )
            )

    rss_result = _parse_rss_items(rss)
    atom_result = _parse_atom_items(atom)
    json_result = _parse_json_items(json_feed)

    if isinstance(rss_result, VerificationViolation):
        violations.append(rss_result)
        rss_items: list[_ItemView] | None = None
    else:
        rss_items = rss_result

    if isinstance(atom_result, VerificationViolation):
        violations.append(atom_result)
        atom_items: list[_ItemView] | None = None
    else:
        atom_items = atom_result

    if isinstance(json_result, VerificationViolation):
        violations.append(json_result)
        json_items: list[_ItemView] | None = None
    else:
        json_items = json_result

    # Structural failures make further parity checks unreliable — still run
    # per-format field checks on whatever parsed successfully.
    for items, path, label, check_ct in (
        (rss_items, "feeds/rss.xml", "RSS", False),
        (atom_items, "feeds/atom.xml", "Atom", False),
        (json_items, "feeds/feed.json", "JSON", True),
    ):
        if items is None:
            continue
        violations.extend(_check_duplicates(items, path=path, format_label=label))
        violations.extend(
            _check_item_fields(
                items,
                path=path,
                format_label=label,
                check_content_text=check_ct,
            )
        )

    if rss_items is not None and atom_items is not None and json_items is not None:
        rss_count = len(rss_items)
        atom_count = len(atom_items)
        json_count = len(json_items)
        if rss_count != atom_count or rss_count != json_count:
            violations.append(
                VerificationViolation(
                    code=COUNT_MISMATCH,
                    message=(
                        f"Feed count mismatch: RSS={rss_count} Atom={atom_count} JSON={json_count}"
                    ),
                )
            )
        else:
            if rss_count < min_items:
                violations.append(
                    VerificationViolation(
                        code=BELOW_MIN_ITEMS,
                        message=f"Feed item count {rss_count} below floor {min_items}",
                    )
                )
            rss_ids = [it.item_id for it in rss_items]
            atom_ids = [it.item_id for it in atom_items]
            json_ids = [it.item_id for it in json_items]
            if not (rss_ids == atom_ids == json_ids):
                violations.append(
                    VerificationViolation(
                        code=ID_ORDER_MISMATCH,
                        message="Ordered id lists differ across RSS/Atom/JSON",
                    )
                )

    return VerificationReport(ok=len(violations) == 0, violations=violations)


def verify_feed_dir(
    root: Path,
    *,
    min_items: int,
    relative_dir: str = "feeds",
) -> VerificationReport:
    """Read ``root / relative_dir / {rss.xml,atom.xml,feed.json}`` and deep-verify."""
    paths = _feed_paths(root, relative_dir=relative_dir)
    violations: list[VerificationViolation] = []
    blobs: dict[str, bytes] = {}
    rel_prefix = relative_dir.strip("/")

    for key, path in paths.items():
        rel = f"{rel_prefix}/{_FEED_NAMES[key]}"
        if not path.is_file():
            violations.append(
                VerificationViolation(
                    code=MISSING_FILE,
                    message=f"Missing feed artifact: {path}",
                    path=rel,
                )
            )
            continue
        try:
            blobs[key] = path.read_bytes()
        except OSError as exc:
            violations.append(
                VerificationViolation(
                    code=MISSING_FILE,
                    message=f"Unreadable feed artifact {path}: {exc}",
                    path=rel,
                )
            )

    if len(blobs) != 3:
        return VerificationReport(ok=False, violations=violations)

    report = verify_feed_bytes(
        rss=blobs["rss"],
        atom=blobs["atom"],
        json_feed=blobs["json"],
        min_items=min_items,
    )
    if not violations:
        return report
    return VerificationReport(
        ok=False,
        violations=[*violations, *report.violations],
    )


def raise_on_failure(report: VerificationReport) -> None:
    """Raise :class:`VerificationError` when ``report`` is not ok.

    Message includes the first few violations for diagnostics.
    """
    if report.ok:
        return
    preview = report.violations[:5]
    parts = [f"{v.code}: {v.message}" for v in preview]
    extra = len(report.violations) - len(preview)
    suffix = f" (+{extra} more)" if extra > 0 else ""
    raise VerificationError(
        f"Feed verification failed ({len(report.violations)}): " + "; ".join(parts) + suffix
    )


def assert_verified(
    *,
    rss: bytes | None = None,
    atom: bytes | None = None,
    json_feed: bytes | None = None,
    root: Path | None = None,
    min_items: int,
) -> VerificationReport:
    """Verify in-memory bytes or an on-disk root; raise on failure.

    Provide either all three byte arguments or ``root`` (not both modes mixed).
    """
    if root is not None:
        if rss is not None or atom is not None or json_feed is not None:
            raise FeedError("assert_verified: pass either root or feed bytes, not both")
        report = verify_feed_dir(root, min_items=min_items)
    else:
        if rss is None or atom is None or json_feed is None:
            raise FeedError("assert_verified: require rss, atom, and json_feed bytes (or root)")
        report = verify_feed_bytes(
            rss=rss,
            atom=atom,
            json_feed=json_feed,
            min_items=min_items,
        )
    raise_on_failure(report)
    return report
