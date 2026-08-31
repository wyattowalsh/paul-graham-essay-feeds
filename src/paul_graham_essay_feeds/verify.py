"""Deep in-memory and on-disk feed bundle verification (F-015, F-003).

Prefer returning :class:`VerificationReport` for testability. Use
:func:`raise_on_failure` (or :func:`assert_verified`) when a hard fail is needed.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Final, Literal, cast
from urllib.parse import urljoin, urlsplit, urlunsplit

from paul_graham_essay_feeds.enrich import score_summary_quality, summary_passes_quality_gate
from paul_graham_essay_feeds.models import (
    ATOM_NS,
    FEED_ID,
    FEED_ID_SIMPLE,
    FEED_SUMMARY_CHARS,
    FeedError,
    VerificationError,
)
from paul_graham_essay_feeds.models import (
    JSON_FEED_VERSION as JSON_FEED_VERSION_IRI,
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
TITLE_ORDER_MISMATCH: Final = "TITLE_ORDER_MISMATCH"
URL_ORDER_MISMATCH: Final = "URL_ORDER_MISMATCH"
SUMMARY_ORDER_MISMATCH: Final = "SUMMARY_ORDER_MISMATCH"
ARTIFACT_TOO_LARGE: Final = "ARTIFACT_TOO_LARGE"
FEED_ROOT: Final = "FEED_ROOT"
RSS_VERSION: Final = "RSS_VERSION"
RSS_CHANNEL: Final = "RSS_CHANNEL"
ATOM_NAMESPACE: Final = "ATOM_NAMESPACE"
ATOM_FEED_COUNT: Final = "ATOM_FEED_COUNT"
ATOM_REQUIRED_ELEMENT: Final = "ATOM_REQUIRED_ELEMENT"
JSON_FEED_VERSION: Final = "JSON_FEED_VERSION"
JSON_FEED_FIELD: Final = "JSON_FEED_FIELD"
INVALID_URI: Final = "INVALID_URI"
INVALID_TIMESTAMP: Final = "INVALID_TIMESTAMP"
SELF_LINK_MISMATCH: Final = "SELF_LINK_MISMATCH"
FEED_ID_COLLISION: Final = "FEED_ID_COLLISION"
VARIANT_IDENTITY: Final = "VARIANT_IDENTITY"
FEED_CLOCK: Final = "FEED_CLOCK"
SEMANTIC_SUMMARY: Final = "SEMANTIC_SUMMARY"
_MAX_ARTIFACT_BYTES: Final = 20 * 1024 * 1024

_REPLACEMENT = "\ufffd"

FeedKind = Literal["enriched", "simple"]


def summary_passes_semantic_gate(
    summary: str | None,
    *,
    score: float | None = None,
    flags: tuple[str, ...] | None = None,
) -> bool:
    """True when ``summary`` is usable enriched feed text (not promo/nav chrome)."""
    return summary_passes_quality_gate(summary, score=score, flags=flags)


def semantic_summary_violations(
    summary: str | None,
    *,
    path: str | None = None,
    index: int | None = None,
) -> list[VerificationViolation]:
    """Return SEMANTIC_SUMMARY violations for promo/navigation-only candidates."""
    scored, flags = score_summary_quality(summary)
    if summary_passes_quality_gate(summary, score=scored, flags=flags):
        return []
    kinds = ", ".join(flags) if flags else "low_quality"
    preview = (summary or "").strip()[:80]
    return [
        VerificationViolation(
            code=SEMANTIC_SUMMARY,
            message=(f"summary fails semantic gate ({kinds}; score={scored:.2f}): {preview!r}"),
            path=path,
            index=index,
        )
    ]


_KNOWN_SELF_BASENAMES: Final[dict[str, dict[FeedKind, str]]] = {
    "rss": {"enriched": "rss.xml", "simple": "rss.simple.xml"},
    "atom": {"enriched": "atom.xml", "simple": "atom.simple.xml"},
    "json": {"enriched": "feed.json", "simple": "feed.simple.json"},
}

_ENRICHED_FEED_NAMES: Final[dict[str, str]] = {
    "rss": "rss.xml",
    "atom": "atom.xml",
    "json": "feed.json",
}
_SIMPLE_FEED_NAMES: Final[dict[str, str]] = {
    "rss": "rss.simple.xml",
    "atom": "atom.simple.xml",
    "json": "feed.simple.json",
}


def _feed_names(kind: FeedKind) -> dict[str, str]:
    return _SIMPLE_FEED_NAMES if kind == "simple" else _ENRICHED_FEED_NAMES


def _rel_feed_path(kind: FeedKind, key: str) -> str:
    return f"feeds/{_feed_names(kind)[key]}"


def _feed_basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


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
    kind: FeedKind = "enriched",
) -> dict[str, Path]:
    feeds_dir = root / relative_dir
    names = _feed_names(kind)
    return {
        "rss": feeds_dir / names["rss"],
        "atom": feeds_dir / names["atom"],
        "json": feeds_dir / names["json"],
    }


def _text(el: ET.Element | None) -> str:
    if el is None or el.text is None:
        return ""
    return el.text


def _parse_rss_items(raw: bytes, *, path: str) -> list[_ItemView] | VerificationViolation:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        name = _feed_basename(path)
        return VerificationViolation(
            code=UNPARSEABLE_XML,
            message=f"{name} is not valid XML: {exc}",
            path=path,
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


def _parse_atom_items(raw: bytes, *, path: str) -> list[_ItemView] | VerificationViolation:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        name = _feed_basename(path)
        return VerificationViolation(
            code=UNPARSEABLE_XML,
            message=f"{name} is not valid XML: {exc}",
            path=path,
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


def _parse_json_items(raw: bytes, *, path: str) -> list[_ItemView] | VerificationViolation:
    name = _feed_basename(path)
    try:
        text = raw.decode("utf-8")
        payload = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return VerificationViolation(
            code=UNPARSEABLE_JSON,
            message=f"{name} is not valid JSON: {exc}",
            path=path,
        )
    if not isinstance(payload, dict):
        return VerificationViolation(
            code=UNPARSEABLE_JSON,
            message=f"{name} root must be an object",
            path=path,
        )
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        return VerificationViolation(
            code=UNPARSEABLE_JSON,
            message=f"{name} missing items array",
            path=path,
        )
    items: list[_ItemView] = []
    for i, raw_item in enumerate(raw_items):
        if not isinstance(raw_item, dict):
            return VerificationViolation(
                code=UNPARSEABLE_JSON,
                message=f"{name} items[{i}] must be an object",
                path=path,
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
    apply_semantic_gate: bool = False,
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
            if apply_semantic_gate:
                out.extend(semantic_summary_violations(summary, path=path, index=i))

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


def _local_name(tag: str) -> str:
    if tag.startswith("{") and "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _namespace(tag: str) -> str | None:
    if tag.startswith("{") and "}" in tag:
        return tag[1 : tag.index("}")]
    return None


def _children(parent: ET.Element, local: str, ns: str | None = None) -> list[ET.Element]:
    want = f"{{{ns}}}{local}" if ns else local
    return [child for child in list(parent) if child.tag == want]


def _xml_root(raw: bytes) -> ET.Element | None:
    try:
        return ET.fromstring(raw)
    except ET.ParseError:
        return None


def _json_object(raw: bytes) -> dict[str, object] | None:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return cast(dict[str, object], payload)


def _is_valid_uri(value: str) -> bool:
    text = value.strip()
    if not text or any(ch.isspace() for ch in text):
        return False
    parts = urlsplit(text)
    scheme = parts.scheme.lower()
    if not scheme:
        return False
    if scheme in {"http", "https", "ftp"}:
        return bool(parts.netloc)
    if scheme in {"mailto", "tag", "urn"}:
        return bool(parts.path)
    return bool(parts.netloc or parts.path)


def _is_rfc822(text: str) -> bool:
    raw = text.strip()
    if not raw:
        return False
    try:
        parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError, OverflowError):
        return False
    return True


def _is_rfc3339(text: str) -> bool:
    raw = text.strip()
    if not raw:
        return False
    candidate = raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _path_basename(url: str) -> str:
    path = urlsplit(url.strip()).path
    return path.rsplit("/", 1)[-1] if path else ""


def _join_base_artifact(base: str, name: str) -> str:
    parts = urlsplit(base.strip())
    directory_path = (parts.path or "").rstrip("/") + "/"
    directory = urlunsplit((parts.scheme, parts.netloc, directory_path, "", ""))
    return urljoin(directory, name)


def _expected_self_map(
    *,
    kind: FeedKind,
    public_base_url: str | None,
    expected_self: dict[str, str] | None,
) -> dict[str, str]:
    out: dict[str, str] = {}
    if public_base_url is not None:
        base = public_base_url.strip()
        if base:
            names = _feed_names(kind)
            out = {key: _join_base_artifact(base, name) for key, name in names.items()}
    if expected_self:
        for key, url in expected_self.items():
            stripped = url.strip()
            if stripped:
                out[key] = stripped
    return out


def _rss_self_href(channel: ET.Element) -> str | None:
    for link in channel.findall(f"{{{ATOM_NS}}}link"):
        if link.attrib.get("rel") == "self":
            href = link.attrib.get("href", "").strip()
            if href:
                return href
    return None


def _atom_self_href(feed: ET.Element) -> str | None:
    for link in _children(feed, "link", ATOM_NS):
        if link.attrib.get("rel") == "self":
            href = link.attrib.get("href", "").strip()
            if href:
                return href
    return None


def _atom_feed_id(raw: bytes) -> str | None:
    root = _xml_root(raw)
    if root is None or _local_name(root.tag) != "feed":
        return None
    ns = _namespace(root.tag)
    els = _children(root, "id", ns) if ns else _children(root, "id")
    if not els and ns != ATOM_NS:
        els = _children(root, "id", ATOM_NS)
    text = _text(els[0]).strip() if els else ""
    return text or None


def _json_feed_url(raw: bytes) -> str | None:
    payload = _json_object(raw)
    if payload is None:
        return None
    value = payload.get("feed_url")
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _self_link_violations(
    *,
    actual: str | None,
    expected: str | None,
    path: str,
    kind: FeedKind,
    format_key: str,
) -> list[VerificationViolation]:
    name = _feed_basename(path)
    out: list[VerificationViolation] = []
    if expected is not None:
        if actual is None or actual != expected:
            got = actual if actual is not None else ""
            out.append(
                VerificationViolation(
                    code=SELF_LINK_MISMATCH,
                    message=f"{name} self/feed URL must equal {expected!r} (got {got!r})",
                    path=path,
                )
            )
        return out
    if not actual:
        return out
    if not _is_valid_uri(actual):
        out.append(
            VerificationViolation(
                code=INVALID_URI,
                message=f"{name} self/feed URL is not a valid URI",
                path=path,
            )
        )
        return out
    names = _KNOWN_SELF_BASENAMES.get(format_key)
    if names is None:
        return out
    got = _path_basename(actual)
    expected_name = names[kind]
    other: FeedKind = "simple" if kind == "enriched" else "enriched"
    if got == names[other] and got != expected_name:
        out.append(
            VerificationViolation(
                code=VARIANT_IDENTITY,
                message=f"{name} self/feed URL basename {got!r} does not match {kind} variant",
                path=path,
            )
        )
    return out


def _check_rss_contract(
    raw: bytes,
    *,
    path: str,
    kind: FeedKind,
    expected_self: str | None,
) -> list[VerificationViolation]:
    root = _xml_root(raw)
    if root is None:
        return []
    name = _feed_basename(path)
    out: list[VerificationViolation] = []
    if _local_name(root.tag) != "rss" or _namespace(root.tag) is not None:
        out.append(
            VerificationViolation(
                code=FEED_ROOT,
                message=f"{name} root element must be un-namespaced rss",
                path=path,
            )
        )
        return out
    version = root.attrib.get("version", "").strip()
    if version != "2.0":
        out.append(
            VerificationViolation(
                code=RSS_VERSION,
                message=f"{name} rss version must be 2.0 (got {version!r})",
                path=path,
            )
        )
    channels = _children(root, "channel")
    if len(channels) != 1:
        out.append(
            VerificationViolation(
                code=RSS_CHANNEL,
                message=f"{name} must contain exactly one channel (got {len(channels)})",
                path=path,
            )
        )
        return out
    channel = channels[0]
    for local in ("title", "link", "description"):
        els = _children(channel, local)
        if len(els) != 1 or not _text(els[0]).strip():
            out.append(
                VerificationViolation(
                    code=RSS_CHANNEL,
                    message=f"{name} channel missing required {local}",
                    path=path,
                )
            )
            continue
        if local == "link":
            href = _text(els[0]).strip()
            if not _is_valid_uri(href):
                out.append(
                    VerificationViolation(
                        code=INVALID_URI,
                        message=f"{name} channel link is not a valid URI",
                        path=path,
                    )
                )
    last_build = _children(channel, "lastBuildDate")
    if last_build:
        stamp = _text(last_build[0])
        if stamp.strip() and not _is_rfc822(stamp):
            out.append(
                VerificationViolation(
                    code=FEED_CLOCK,
                    message=f"{name} lastBuildDate is not a parseable RFC822 date",
                    path=path,
                )
            )
    out.extend(
        _self_link_violations(
            actual=_rss_self_href(channel),
            expected=expected_self,
            path=path,
            kind=kind,
            format_key="rss",
        )
    )
    for i, item in enumerate(channel.findall("item")):
        guid = _text(item.find("guid")).strip()
        link = _text(item.find("link")).strip()
        if guid and not _is_valid_uri(guid):
            out.append(
                VerificationViolation(
                    code=INVALID_URI,
                    message=f"{name} items[{i}] guid is not a valid URI",
                    path=path,
                    index=i,
                )
            )
        if link and not _is_valid_uri(link):
            out.append(
                VerificationViolation(
                    code=INVALID_URI,
                    message=f"{name} items[{i}] link is not a valid URI",
                    path=path,
                    index=i,
                )
            )
        pub = item.find("pubDate")
        if pub is not None:
            stamp = _text(pub)
            if stamp.strip() and not _is_rfc822(stamp):
                out.append(
                    VerificationViolation(
                        code=INVALID_TIMESTAMP,
                        message=f"{name} items[{i}] pubDate is not a parseable RFC822 date",
                        path=path,
                        index=i,
                    )
                )
    return out


def _check_atom_contract(
    raw: bytes,
    *,
    path: str,
    kind: FeedKind,
    expected_self: str | None,
) -> list[VerificationViolation]:
    root = _xml_root(raw)
    if root is None:
        return []
    name = _feed_basename(path)
    out: list[VerificationViolation] = []
    atom_feeds: list[ET.Element] = []
    if _local_name(root.tag) == "feed" and _namespace(root.tag) == ATOM_NS:
        atom_feeds.append(root)
    atom_feeds.extend(root.findall(f".//{{{ATOM_NS}}}feed"))
    if _namespace(root.tag) != ATOM_NS or _local_name(root.tag) != "feed":
        out.append(
            VerificationViolation(
                code=ATOM_NAMESPACE,
                message=f"{name} default namespace must be {ATOM_NS} on the feed element",
                path=path,
            )
        )
    if len(atom_feeds) != 1:
        out.append(
            VerificationViolation(
                code=ATOM_FEED_COUNT,
                message=f"{name} must contain exactly one Atom feed (got {len(atom_feeds)})",
                path=path,
            )
        )
    if _local_name(root.tag) != "feed" or _namespace(root.tag) != ATOM_NS:
        return out
    feed = root
    expected_id = FEED_ID_SIMPLE if kind == "simple" else FEED_ID
    for local in ("id", "title", "updated"):
        els = _children(feed, local, ATOM_NS)
        if len(els) != 1:
            out.append(
                VerificationViolation(
                    code=ATOM_REQUIRED_ELEMENT,
                    message=f"{name} must contain exactly one {local} (got {len(els)})",
                    path=path,
                )
            )
            continue
        value = _text(els[0]).strip()
        if not value:
            out.append(
                VerificationViolation(
                    code=ATOM_REQUIRED_ELEMENT,
                    message=f"{name} feed {local} is empty",
                    path=path,
                )
            )
            continue
        if local == "id":
            if not _is_valid_uri(value):
                out.append(
                    VerificationViolation(
                        code=INVALID_URI,
                        message=f"{name} feed id is not a valid URI",
                        path=path,
                    )
                )
            if value != expected_id and not (kind == "simple" and value == FEED_ID):
                # Simple triples that still carry FEED_ID are common write_feeds
                # fixtures used by CLI ``check``. Directory verification still
                # flags FEED_ID_COLLISION / VARIANT_IDENTITY when both variants exist.
                out.append(
                    VerificationViolation(
                        code=VARIANT_IDENTITY,
                        message=(
                            f"{name} feed id must be {expected_id!r} for {kind} (got {value!r})"
                        ),
                        path=path,
                    )
                )
        elif local == "updated" and not _is_rfc3339(value):
            out.append(
                VerificationViolation(
                    code=FEED_CLOCK,
                    message=f"{name} feed updated is not a parseable RFC3339 timestamp",
                    path=path,
                )
            )
    authors = _children(feed, "author", ATOM_NS)
    entries = _children(feed, "entry", ATOM_NS)
    if not authors:
        missing_entry_author = not entries or any(
            not _children(entry, "author", ATOM_NS) for entry in entries
        )
        if missing_entry_author:
            out.append(
                VerificationViolation(
                    code=ATOM_REQUIRED_ELEMENT,
                    message=f"{name} feed must have author (RFC 4287) unless every entry has one",
                    path=path,
                )
            )
    else:
        for author in authors:
            if not _text(author.find(f"{{{ATOM_NS}}}name")).strip():
                out.append(
                    VerificationViolation(
                        code=ATOM_REQUIRED_ELEMENT,
                        message=f"{name} author must contain a name",
                        path=path,
                    )
                )
                break
    out.extend(
        _self_link_violations(
            actual=_atom_self_href(feed),
            expected=expected_self,
            path=path,
            kind=kind,
            format_key="atom",
        )
    )
    for i, entry in enumerate(entries):
        for local in ("id", "title", "updated"):
            els = _children(entry, local, ATOM_NS)
            if len(els) != 1:
                out.append(
                    VerificationViolation(
                        code=ATOM_REQUIRED_ELEMENT,
                        message=(
                            f"{name} entries[{i}] must contain exactly one {local} (got {len(els)})"
                        ),
                        path=path,
                        index=i,
                    )
                )
                continue
            value = _text(els[0]).strip()
            if local == "id" and value and not _is_valid_uri(value):
                out.append(
                    VerificationViolation(
                        code=INVALID_URI,
                        message=f"{name} entries[{i}] id is not a valid URI",
                        path=path,
                        index=i,
                    )
                )
            if local == "updated" and value and not _is_rfc3339(value):
                out.append(
                    VerificationViolation(
                        code=INVALID_TIMESTAMP,
                        message=f"{name} entries[{i}] updated is not a parseable RFC3339 timestamp",
                        path=path,
                        index=i,
                    )
                )
        published_els = _children(entry, "published", ATOM_NS)
        if published_els:
            stamp = _text(published_els[0])
            if stamp.strip() and not _is_rfc3339(stamp):
                out.append(
                    VerificationViolation(
                        code=INVALID_TIMESTAMP,
                        message=(
                            f"{name} entries[{i}] published is not a parseable RFC3339 timestamp"
                        ),
                        path=path,
                        index=i,
                    )
                )
        for link in _children(entry, "link", ATOM_NS):
            href = link.attrib.get("href", "").strip()
            if href and not _is_valid_uri(href):
                out.append(
                    VerificationViolation(
                        code=INVALID_URI,
                        message=f"{name} entries[{i}] link href is not a valid URI",
                        path=path,
                        index=i,
                    )
                )
    return out


def _check_json_contract(
    raw: bytes,
    *,
    path: str,
    kind: FeedKind,
    expected_self: str | None,
) -> list[VerificationViolation]:
    payload = _json_object(raw)
    if payload is None:
        return []
    name = _feed_basename(path)
    out: list[VerificationViolation] = []
    version = payload.get("version")
    if not isinstance(version, str) or version != JSON_FEED_VERSION_IRI:
        got = version if isinstance(version, str) else type(version).__name__
        out.append(
            VerificationViolation(
                code=JSON_FEED_VERSION,
                message=f"{name} version must be {JSON_FEED_VERSION_IRI!r} (got {got!r})",
                path=path,
            )
        )
    title = payload.get("title")
    if not isinstance(title, str) or not title.strip():
        out.append(
            VerificationViolation(
                code=JSON_FEED_FIELD,
                message=f"{name} missing required string title",
                path=path,
            )
        )
    home = payload.get("home_page_url")
    if not isinstance(home, str) or not home.strip():
        out.append(
            VerificationViolation(
                code=JSON_FEED_FIELD,
                message=f"{name} missing required string home_page_url",
                path=path,
            )
        )
    elif not _is_valid_uri(home):
        out.append(
            VerificationViolation(
                code=INVALID_URI,
                message=f"{name} home_page_url is not a valid URI",
                path=path,
            )
        )
    items = payload.get("items")
    if not isinstance(items, list):
        out.append(
            VerificationViolation(
                code=JSON_FEED_FIELD,
                message=f"{name} missing required items array",
                path=path,
            )
        )
        items = []
    feed_url = payload.get("feed_url")
    if "feed_url" in payload and not isinstance(feed_url, str):
        out.append(
            VerificationViolation(
                code=JSON_FEED_FIELD,
                message=f"{name} feed_url must be a string when present",
                path=path,
            )
        )
        actual_self = None
    else:
        actual_self = feed_url.strip() if isinstance(feed_url, str) and feed_url.strip() else None
    out.extend(
        _self_link_violations(
            actual=actual_self,
            expected=expected_self,
            path=path,
            kind=kind,
            format_key="json",
        )
    )
    for i, raw_item in enumerate(items):
        if not isinstance(raw_item, dict):
            continue
        item = cast(dict[str, object], raw_item)
        url = item.get("url")
        if isinstance(url, str) and url.strip() and not _is_valid_uri(url):
            out.append(
                VerificationViolation(
                    code=INVALID_URI,
                    message=f"{name} items[{i}] url is not a valid URI",
                    path=path,
                    index=i,
                )
            )
        for key in ("date_published", "date_modified"):
            value = item.get(key)
            if isinstance(value, str) and value.strip() and not _is_rfc3339(value):
                out.append(
                    VerificationViolation(
                        code=INVALID_TIMESTAMP,
                        message=f"{name} items[{i}] {key} is not a parseable RFC3339 timestamp",
                        path=path,
                        index=i,
                    )
                )
    return out


def _load_catalog_hints(root: Path) -> tuple[list[str] | None, str | None]:
    catalog_path = root / "catalog.json"
    if not catalog_path.is_file():
        return None, None
    try:
        payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, None
    if not isinstance(payload, dict):
        return None, None
    order_raw = payload.get("entry_order")
    order: list[str] | None = None
    if isinstance(order_raw, list):
        ids = [item for item in order_raw if isinstance(item, str)]
        if len(ids) == len(order_raw):
            order = ids
    base = payload.get("public_base_url")
    public = base.strip() if isinstance(base, str) and base.strip() else None
    return order, public


def _check_dir_catalog_order(
    root: Path,
    *,
    relative_dir: str,
    catalog_ids: list[str],
) -> list[VerificationViolation]:
    out: list[VerificationViolation] = []
    rel_prefix = relative_dir.strip("/")
    parsers = (
        ("rss", _parse_rss_items),
        ("atom", _parse_atom_items),
        ("json", _parse_json_items),
    )
    kinds: tuple[FeedKind, ...] = ("enriched", "simple")
    for kind in kinds:
        paths = _feed_paths(root, relative_dir=relative_dir, kind=kind)
        names = _feed_names(kind)
        for key, parser in parsers:
            path = paths[key]
            if not path.is_file():
                continue
            rel = f"{rel_prefix}/{names[key]}"
            try:
                raw = path.read_bytes()
            except OSError:
                continue
            result = parser(raw, path=rel)
            if isinstance(result, VerificationViolation):
                continue
            ids = [item.item_id for item in result]
            if ids != catalog_ids:
                out.append(
                    VerificationViolation(
                        code=ID_ORDER_MISMATCH,
                        message=(
                            "Catalog entry_order ids do not match ordered ids in "
                            f"{rel} (catalog={len(catalog_ids)}, feed={len(ids)})"
                        ),
                        path=rel,
                    )
                )
    return out


def _check_dir_variant_collisions(
    root: Path,
    *,
    relative_dir: str,
    kind: FeedKind,
) -> list[VerificationViolation]:
    out: list[VerificationViolation] = []
    enriched_paths = _feed_paths(root, relative_dir=relative_dir, kind="enriched")
    simple_paths = _feed_paths(root, relative_dir=relative_dir, kind="simple")
    if enriched_paths["atom"].is_file() and simple_paths["atom"].is_file():
        try:
            enriched_id = _atom_feed_id(enriched_paths["atom"].read_bytes())
            simple_id = _atom_feed_id(simple_paths["atom"].read_bytes())
        except OSError:
            pass
        else:
            if enriched_id and simple_id and enriched_id == simple_id:
                out.append(
                    VerificationViolation(
                        code=FEED_ID_COLLISION,
                        message=(
                            "Atom feed ids collide across enriched and simple variants: "
                            f"{enriched_id!r}"
                        ),
                        path=_rel_feed_path("simple", "atom"),
                    )
                )
    if enriched_paths["json"].is_file() and simple_paths["json"].is_file():
        try:
            enriched_url = _json_feed_url(enriched_paths["json"].read_bytes())
            simple_url = _json_feed_url(simple_paths["json"].read_bytes())
        except OSError:
            pass
        else:
            if enriched_url and simple_url and enriched_url == simple_url:
                out.append(
                    VerificationViolation(
                        code=FEED_ID_COLLISION,
                        message=(
                            "JSON feed_url values collide across enriched and simple variants: "
                            f"{enriched_url!r}"
                        ),
                        path=_rel_feed_path("simple", "json"),
                    )
                )
    other: FeedKind = "simple" if kind == "enriched" else "enriched"
    other_paths = simple_paths if other == "simple" else enriched_paths
    if other_paths["atom"].is_file():
        try:
            other_id = _atom_feed_id(other_paths["atom"].read_bytes())
        except OSError:
            other_id = None
        expected = FEED_ID_SIMPLE if other == "simple" else FEED_ID
        if other_id and other_id != expected:
            out.append(
                VerificationViolation(
                    code=VARIANT_IDENTITY,
                    message=(
                        f"{_feed_names(other)['atom']} feed id must be {expected!r} "
                        f"for {other} (got {other_id!r})"
                    ),
                    path=_rel_feed_path(other, "atom"),
                )
            )
    return out


def verify_feed_bytes(
    *,
    rss: bytes,
    atom: bytes,
    json_feed: bytes,
    min_items: int,
    kind: FeedKind = "enriched",
    public_base_url: str | None = None,
    expected_self: dict[str, str] | None = None,
) -> VerificationReport:
    """Deep-verify an in-memory RSS/Atom/JSON feed triple.

    Checks parseability, size caps, count parity, min-items floor, duplicate
    ids, empty id/title/url/summary, JSON ``content_text == summary``, summary
    length bounds, U+FFFD integrity, ordered id parity, and ordered
    title/url/summary payload parity across formats. Enriched triples also
    apply the semantic summary gate (promo/nav chrome). Independently checks
    feed-level RSS/Atom/JSON contracts (root, namespace, version, clocks,
    URI syntax, exact self links when known, and variant identity).

    ``kind`` selects violation ``path`` labels (``feeds/rss.xml`` vs
    ``feeds/rss.simple.xml`` and siblings). ``public_base_url`` / ``expected_self``
    require exact self/feed URLs (not substring matches) when provided.
    """
    rss_path = _rel_feed_path(kind, "rss")
    atom_path = _rel_feed_path(kind, "atom")
    json_path = _rel_feed_path(kind, "json")
    violations: list[VerificationViolation] = []
    for label, blob, path in (
        ("rss", rss, rss_path),
        ("atom", atom, atom_path),
        ("json", json_feed, json_path),
    ):
        if len(blob) > _MAX_ARTIFACT_BYTES:
            violations.append(
                VerificationViolation(
                    code=ARTIFACT_TOO_LARGE,
                    message=(f"{label} artifact is {len(blob)} bytes (max {_MAX_ARTIFACT_BYTES})"),
                    path=path,
                )
            )

    rss_result = _parse_rss_items(rss, path=rss_path)
    atom_result = _parse_atom_items(atom, path=atom_path)
    json_result = _parse_json_items(json_feed, path=json_path)

    expected = _expected_self_map(
        kind=kind,
        public_base_url=public_base_url,
        expected_self=expected_self,
    )
    violations.extend(
        _check_rss_contract(
            rss,
            path=rss_path,
            kind=kind,
            expected_self=expected.get("rss"),
        )
    )
    violations.extend(
        _check_atom_contract(
            atom,
            path=atom_path,
            kind=kind,
            expected_self=expected.get("atom"),
        )
    )
    violations.extend(
        _check_json_contract(
            json_feed,
            path=json_path,
            kind=kind,
            expected_self=expected.get("json"),
        )
    )

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
        (rss_items, rss_path, "RSS", False),
        (atom_items, atom_path, "Atom", False),
        (json_items, json_path, "JSON", True),
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
                apply_semantic_gate=(kind == "enriched"),
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
            else:
                # Cross-format payload parity (RES-H06) after ids align.
                for i, (r, a, j) in enumerate(zip(rss_items, atom_items, json_items, strict=True)):
                    if not (r.title == a.title == j.title):
                        violations.append(
                            VerificationViolation(
                                code=TITLE_ORDER_MISMATCH,
                                message=(
                                    f"Ordered titles differ at index {i} across RSS/Atom/JSON"
                                ),
                                index=i,
                            )
                        )
                    if not (r.url == a.url == j.url):
                        violations.append(
                            VerificationViolation(
                                code=URL_ORDER_MISMATCH,
                                message=(f"Ordered urls differ at index {i} across RSS/Atom/JSON"),
                                index=i,
                            )
                        )
                    if not (r.summary == a.summary == j.summary):
                        violations.append(
                            VerificationViolation(
                                code=SUMMARY_ORDER_MISMATCH,
                                message=(
                                    f"Ordered summaries differ at index {i} across RSS/Atom/JSON"
                                ),
                                index=i,
                            )
                        )

    return VerificationReport(ok=len(violations) == 0, violations=violations)


def verify_feed_dir(
    root: Path,
    *,
    min_items: int,
    relative_dir: str = "feeds",
    kind: FeedKind = "enriched",
    public_base_url: str | None = None,
    expected_self: dict[str, str] | None = None,
) -> VerificationReport:
    """Read one on-disk feed triple and deep-verify.

    ``kind`` selects filenames (``rss.xml`` vs ``rss.simple.xml`` and siblings)
    and the violation ``path`` labels passed to :func:`verify_feed_bytes`.
    When ``catalog.json`` is present, ``entry_order`` is compared to item id
    sequences in every existing feed among the six artifacts. Sibling
    enriched/simple Atom ids and JSON ``feed_url`` values must not collide.
    ``public_base_url`` is taken from the argument, else from catalog when present.
    """
    paths = _feed_paths(root, relative_dir=relative_dir, kind=kind)
    violations: list[VerificationViolation] = []
    blobs: dict[str, bytes] = {}
    names = _feed_names(kind)
    rel_prefix = relative_dir.strip("/")
    catalog_ids, catalog_base = _load_catalog_hints(root)
    if public_base_url is None:
        public_base_url = catalog_base

    for key, path in paths.items():
        rel = f"{rel_prefix}/{names[key]}"
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

    extras: list[VerificationViolation] = []
    extras.extend(_check_dir_variant_collisions(root, relative_dir=relative_dir, kind=kind))
    if catalog_ids is not None:
        extras.extend(
            _check_dir_catalog_order(
                root,
                relative_dir=relative_dir,
                catalog_ids=catalog_ids,
            )
        )

    if len(blobs) != 3:
        return VerificationReport(ok=False, violations=[*violations, *extras])

    report = verify_feed_bytes(
        rss=blobs["rss"],
        atom=blobs["atom"],
        json_feed=blobs["json"],
        min_items=min_items,
        kind=kind,
        public_base_url=public_base_url,
        expected_self=expected_self,
    )
    merged = [*violations, *report.violations, *extras]
    return VerificationReport(ok=len(merged) == 0, violations=merged)


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
    kind: FeedKind = "enriched",
    public_base_url: str | None = None,
    expected_self: dict[str, str] | None = None,
) -> VerificationReport:
    """Verify in-memory bytes or an on-disk root; raise on failure.

    Provide either all three byte arguments or ``root`` (not both modes mixed).
    ``kind`` selects violation ``path`` labels (and on-disk filenames when
    ``root`` is used).
    """
    if root is not None:
        if rss is not None or atom is not None or json_feed is not None:
            raise FeedError("assert_verified: pass either root or feed bytes, not both")
        report = verify_feed_dir(
            root,
            min_items=min_items,
            kind=kind,
            public_base_url=public_base_url,
            expected_self=expected_self,
        )
    else:
        if rss is None or atom is None or json_feed is None:
            raise FeedError("assert_verified: require rss, atom, and json_feed bytes (or root)")
        report = verify_feed_bytes(
            rss=rss,
            atom=atom,
            json_feed=json_feed,
            min_items=min_items,
            kind=kind,
            public_base_url=public_base_url,
            expected_self=expected_self,
        )
    raise_on_failure(report)
    return report
