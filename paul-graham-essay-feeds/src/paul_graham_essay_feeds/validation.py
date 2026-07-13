"""Per-format and cross-format validation."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from typing import Any

from paul_graham_essay_feeds.domain import (
    ATOM_NS,
    CHANNEL_URL,
    DC_NS,
    JSON_FEED_VERSION,
    EssayItem,
    FeedError,
    PublicUrls,
    ValidationReport,
    normalize_text,
    utc_now,
)

__all__ = [
    "assert_cross_format_parity",
    "build_validation_report",
    "validate_all_formats",
    "validate_atom_bytes",
    "validate_json_feed_bytes",
    "validate_opml_bytes",
    "validate_rss_bytes",
]


def _parse_xml(xml_bytes: bytes) -> ET.Element:
    try:
        return ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise FeedError(f"Generated XML is not well-formed: {exc}") from exc


def validate_rss_bytes(
    xml_bytes: bytes,
    *,
    expected_items: Sequence[EssayItem],
    min_items: int,
    public: PublicUrls | None,
    generator: str,
) -> dict[str, Any]:
    """Validate RSS 2.0 structure and exact item alignment."""
    errors: list[str] = []
    root = _parse_xml(xml_bytes)
    if root.tag != "rss":
        errors.append(f"Root element must be rss, found {root.tag!r}.")
    if root.attrib.get("version") != "2.0":
        errors.append("rss@version must be exactly '2.0'.")

    channels = root.findall("channel")
    if len(channels) != 1:
        errors.append(f"Expected exactly one channel, found {len(channels)}.")
        channel = channels[0] if channels else None
    else:
        channel = channels[0]

    parsed_rows: list[tuple[str, str, str, bool]] = []
    if channel is not None:
        for tag in ("title", "link", "description"):
            nodes = channel.findall(tag)
            if len(nodes) != 1 or not normalize_text(nodes[0].text or ""):
                errors.append(f"Channel requires exactly one non-empty {tag} element.")

        if normalize_text(channel.findtext("link") or "") != CHANNEL_URL:
            errors.append(f"Channel link must be the official essays index: {CHANNEL_URL}")

        language = normalize_text(channel.findtext("language") or "")
        if language != "en-US":
            errors.append("Channel language must be exactly 'en-US'.")

        gen = normalize_text(channel.findtext("generator") or "")
        if gen != generator:
            errors.append(f"Channel generator must be {generator}.")

        if not normalize_text(channel.findtext("lastBuildDate") or ""):
            errors.append("Channel must include lastBuildDate.")

        self_links = channel.findall(f"{{{ATOM_NS}}}link")
        if public is not None:
            if len(self_links) != 1 or self_links[0].attrib.get("rel") != "self":
                errors.append("Expected exactly one atom:link rel='self'.")
            elif self_links[0].attrib.get("href") != public.rss:
                errors.append("atom:link href does not match configured RSS public URL.")
        elif self_links:
            errors.append(
                "Feed contains atom:link rel='self' but no deployment URL was configured."
            )

        for index, item in enumerate(channel.findall("item"), start=1):
            titles = item.findall("title")
            links = item.findall("link")
            guids = item.findall("guid")
            if len(titles) != 1 or len(links) != 1 or len(guids) != 1:
                errors.append(f"Item {index} must contain title, link, and guid.")
                continue
            title = normalize_text(titles[0].text or "")
            link = normalize_text(links[0].text or "")
            guid = normalize_text(guids[0].text or "")
            is_permalink = guids[0].attrib.get("isPermaLink", "true").lower() == "true"
            if item.find("description") is None:
                errors.append(f"Item {index} missing description.")
            if item.find(f"{{{DC_NS}}}creator") is None and item.find("dc:creator") is None:
                # ElementTree may expand ns; also try local after register
                creators = [child for child in list(item) if child.tag.endswith("creator")]
                if not creators:
                    errors.append(f"Item {index} missing dc:creator.")
            if item.find("category") is None:
                errors.append(f"Item {index} missing category.")
            if "pubDate" in {child.tag for child in list(item)}:
                errors.append(f"Item {index} must not include pubDate.")
            parsed_rows.append((title, link, guid, is_permalink))

    expected_rows = [
        (item.title, item.url, item.stable_id, item.is_permalink) for item in expected_items
    ]
    if parsed_rows != expected_rows:
        errors.append(
            "RSS item titles, links, IDs, or ordering do not exactly match the "
            "canonical item sequence."
        )
    if len(parsed_rows) < min_items:
        errors.append(f"Feed has {len(parsed_rows)} items, below safety floor {min_items}.")
    if errors:
        raise FeedError("RSS validation failed:\n- " + "\n- ".join(errors))
    return {
        "ok": True,
        "item_count": len(parsed_rows),
        "errors": [],
    }


def validate_atom_bytes(
    xml_bytes: bytes,
    *,
    expected_items: Sequence[EssayItem],
    min_items: int,
    public: PublicUrls | None,
    feed_id: str,
) -> dict[str, Any]:
    """Validate Atom 1.0 required feed/entry elements and parity."""
    errors: list[str] = []
    root = _parse_xml(xml_bytes)
    local = root.tag.split("}")[-1]
    if local != "feed":
        errors.append(f"Root element must be feed, found {root.tag!r}.")

    def children(tag: str) -> list[ET.Element]:
        return [
            el
            for el in list(root)
            if el.tag == tag or el.tag.endswith("}" + tag) or el.tag == f"{{{ATOM_NS}}}{tag}"
        ]

    for required in ("id", "title", "updated"):
        nodes = children(required)
        if len(nodes) != 1 or not normalize_text(nodes[0].text or ""):
            errors.append(f"Feed requires exactly one non-empty {required}.")

    ids = children("id")
    if ids and normalize_text(ids[0].text or "") != feed_id:
        errors.append("Feed id does not match configured feed_id.")

    authors = children("author")
    if not authors:
        errors.append("Feed requires at least one author.")

    entries = [
        el for el in list(root) if el.tag.endswith("entry") or el.tag == f"{{{ATOM_NS}}}entry"
    ]
    parsed: list[tuple[str, str, str]] = []
    for index, entry in enumerate(entries, start=1):
        nodes = list(entry)
        title_nodes = [el for el in nodes if el.tag.endswith("title")]
        id_nodes = [el for el in nodes if el.tag.endswith("id")]
        updated_nodes = [el for el in nodes if el.tag.endswith("updated")]
        if len(title_nodes) != 1 or len(id_nodes) != 1 or len(updated_nodes) != 1:
            errors.append(f"Entry {index} requires id, title, and updated.")
            continue
        if any(el.tag.endswith("published") for el in nodes):
            errors.append(f"Entry {index} must not include published.")
        links = [el for el in nodes if el.tag.endswith("link")]
        if not any(link.attrib.get("rel", "alternate") == "alternate" for link in links):
            errors.append(f"Entry {index} requires an alternate link.")
        href = ""
        for link in links:
            if link.attrib.get("rel", "alternate") == "alternate":
                href = link.attrib.get("href", "")
                break
        parsed.append(
            (
                normalize_text(title_nodes[0].text or ""),
                href,
                normalize_text(id_nodes[0].text or ""),
            )
        )

    expected = [(item.title, item.url, item.stable_id) for item in expected_items]
    if parsed != expected:
        errors.append("Atom entries do not match the canonical item sequence.")
    if len(parsed) < min_items:
        errors.append(f"Atom feed has {len(parsed)} entries, below floor {min_items}.")

    if public is not None:
        self_links = [el for el in children("link") if el.attrib.get("rel") == "self"]
        if len(self_links) != 1 or self_links[0].attrib.get("href") != public.atom:
            errors.append("Atom self link missing or incorrect.")
    else:
        if any(el.attrib.get("rel") == "self" for el in children("link")):
            errors.append("Atom self link present without public base URL.")

    if errors:
        raise FeedError("Atom validation failed:\n- " + "\n- ".join(errors))
    return {"ok": True, "item_count": len(parsed), "errors": []}


def validate_json_feed_bytes(
    raw: bytes,
    *,
    expected_items: Sequence[EssayItem],
    min_items: int,
    public: PublicUrls | None,
) -> dict[str, Any]:
    """Validate JSON Feed 1.1 structure and item parity."""
    errors: list[str] = []
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FeedError(f"JSON Feed is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise FeedError("JSON Feed root must be an object.")

    if data.get("version") != JSON_FEED_VERSION:
        errors.append(f"version must be {JSON_FEED_VERSION!r}.")
    if not normalize_text(str(data.get("title") or "")):
        errors.append("title is required.")
    items = data.get("items")
    if not isinstance(items, list):
        errors.append("items must be an array.")
        items = []

    if "date_published" in data or "date_modified" in data:
        errors.append("Top-level publication dates must not be fabricated.")
    authors = data.get("authors")
    if not isinstance(authors, list) or not authors:
        errors.append("authors array is required.")

    if public is not None:
        if data.get("feed_url") != public.json_feed:
            errors.append("feed_url missing or incorrect.")
    elif data.get("feed_url"):
        errors.append("feed_url present without public base URL.")

    parsed: list[tuple[str, str, str]] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            errors.append(f"Item {index} must be an object.")
            continue
        item_id = str(item.get("id") or "").strip()
        url = str(item.get("url") or "").strip()
        title = normalize_text(str(item.get("title") or ""))
        content_text = str(item.get("content_text") or "").strip()
        content_html = str(item.get("content_html") or "").strip()
        if not item_id:
            errors.append(f"Item {index} missing id.")
        if not content_text and not content_html:
            errors.append(f"Item {index} requires content_text or content_html.")
        if "date_published" in item or "date_modified" in item:
            errors.append(f"Item {index} must not include fabricated dates.")
        parsed.append((title, url, item_id))

    expected = [(item.title, item.url, item.stable_id) for item in expected_items]
    if parsed != expected:
        errors.append("JSON Feed items do not match the canonical item sequence.")
    if len(parsed) < min_items:
        errors.append(f"JSON Feed has {len(parsed)} items, below floor {min_items}.")

    if errors:
        raise FeedError("JSON Feed validation failed:\n- " + "\n- ".join(errors))
    return {"ok": True, "item_count": len(parsed), "errors": []}


def validate_opml_bytes(
    xml_bytes: bytes,
    *,
    public: PublicUrls,
) -> dict[str, Any]:
    """Validate OPML 2.0 subscription catalog structure."""
    errors: list[str] = []
    root = _parse_xml(xml_bytes)
    if root.tag != "opml":
        errors.append(f"Root must be opml, found {root.tag!r}.")
    if root.attrib.get("version") != "2.0":
        errors.append("opml@version must be 2.0.")
    head = root.find("head")
    body = root.find("body")
    if head is None or body is None:
        errors.append("OPML requires head and body.")
    outlines = body.findall("outline") if body is not None else []
    if len(outlines) < 2:
        errors.append("OPML body must catalog at least RSS and Atom.")
    xml_urls = {o.attrib.get("xmlUrl") for o in outlines if o.attrib.get("xmlUrl")}
    link_urls = {o.attrib.get("url") for o in outlines if o.attrib.get("type") == "link"}
    if public.rss not in xml_urls:
        errors.append("OPML missing RSS xmlUrl.")
    if public.atom not in xml_urls:
        errors.append("OPML missing Atom xmlUrl.")
    if public.json_feed not in link_urls:
        errors.append("OPML missing JSON Feed link outline.")
    for outline in outlines:
        if not outline.attrib.get("text"):
            errors.append("Every outline requires text.")
        if outline.attrib.get("type") == "rss" and not outline.attrib.get("xmlUrl"):
            errors.append("RSS outline requires xmlUrl.")
    if errors:
        raise FeedError("OPML validation failed:\n- " + "\n- ".join(errors))
    return {"ok": True, "outline_count": len(outlines), "errors": []}


def assert_cross_format_parity(
    *,
    items: Sequence[EssayItem],
    rss: bytes,
    atom: bytes,
    json_feed: bytes,
) -> dict[str, bool]:
    """Ensure RSS, Atom, and JSON Feed share count/order/title/url/id."""
    rss_root = _parse_xml(rss)
    channel = rss_root.find("channel")
    if channel is None:
        raise FeedError("RSS missing channel for parity check.")
    rss_rows = [
        (
            normalize_text(item.findtext("title") or ""),
            normalize_text(item.findtext("link") or ""),
            normalize_text(item.findtext("guid") or ""),
        )
        for item in channel.findall("item")
    ]

    atom_root = _parse_xml(atom)
    atom_entries = [el for el in list(atom_root) if el.tag.endswith("entry")]
    atom_rows: list[tuple[str, str, str]] = []
    for entry in atom_entries:
        title = ""
        item_id = ""
        href = ""
        for child in list(entry):
            if child.tag.endswith("title"):
                title = normalize_text(child.text or "")
            elif child.tag.endswith("id"):
                item_id = normalize_text(child.text or "")
            elif child.tag.endswith("link") and child.attrib.get("rel", "alternate") == "alternate":
                href = child.attrib.get("href", "")
        atom_rows.append((title, href, item_id))

    data = json.loads(json_feed.decode("utf-8"))
    json_rows = [
        (
            normalize_text(str(item.get("title") or "")),
            str(item.get("url") or "").strip(),
            str(item.get("id") or "").strip(),
        )
        for item in data.get("items", [])
    ]

    expected = [(i.title, i.url, i.stable_id) for i in items]
    parity = {
        "count": len(rss_rows) == len(atom_rows) == len(json_rows) == len(expected),
        "order": rss_rows == atom_rows == json_rows == expected,
        "titles": [r[0] for r in rss_rows]
        == [r[0] for r in atom_rows]
        == [r[0] for r in json_rows],
        "urls": [r[1] for r in rss_rows] == [r[1] for r in atom_rows] == [r[1] for r in json_rows],
        "ids": [r[2] for r in rss_rows] == [r[2] for r in atom_rows] == [r[2] for r in json_rows],
    }
    if not all(parity.values()):
        raise FeedError(f"Cross-format parity failed: {parity}")
    return parity


def validate_all_formats(
    *,
    items: Sequence[EssayItem],
    rss: bytes,
    atom: bytes,
    json_feed: bytes,
    opml: bytes,
    min_items: int,
    public: PublicUrls,
    generator: str,
    feed_id: str,
) -> dict[str, Any]:
    """Run full multi-format validation for a complete publish set."""
    results = {
        "rss": validate_rss_bytes(
            rss,
            expected_items=items,
            min_items=min_items,
            public=public,
            generator=generator,
        ),
        "atom": validate_atom_bytes(
            atom,
            expected_items=items,
            min_items=min_items,
            public=public,
            feed_id=feed_id,
        ),
        "json_feed": validate_json_feed_bytes(
            json_feed,
            expected_items=items,
            min_items=min_items,
            public=public,
        ),
        "opml": validate_opml_bytes(opml, public=public),
    }
    parity = assert_cross_format_parity(items=items, rss=rss, atom=atom, json_feed=json_feed)
    return {"formats": results, "parity": parity}


def build_validation_report(
    *,
    status: str,
    items: Sequence[EssayItem],
    source_url: str,
    source_sha256: str | None,
    extraction: dict[str, Any] | None,
    changes: dict[str, Any] | None,
    format_hashes: dict[str, dict[str, Any]],
    parity: dict[str, bool],
    errors: list[str] | None = None,
    warnings: list[str] | None = None,
) -> ValidationReport:
    """Assemble a :class:`ValidationReport` for persistence."""
    err = errors or []
    first = None
    last = None
    if items:
        first = {
            "title": items[0].title,
            "url": items[0].url,
            "stable_id": items[0].stable_id,
        }
        last = {
            "title": items[-1].title,
            "url": items[-1].url,
            "stable_id": items[-1].stable_id,
        }
    return ValidationReport(
        valid=not err,
        status=status,
        validated_at=utc_now(),
        source_url=source_url,
        source_sha256=source_sha256,
        extraction=extraction,
        changes=changes,
        item_count=len(items),
        formats=format_hashes,
        parity=parity,
        first_item=first,
        last_item=last,
        checks={
            "minimum_item_floor_met": len(items) >= 1,
            "unique_urls": len({i.url for i in items}) == len(items),
            "unique_ids": len({i.stable_id for i in items}) == len(items),
            "parity_ok": all(parity.values()) if parity else False,
            "no_double_prefixed_urls": not any("paulgraham.com/https://" in i.url for i in items),
        },
        errors=err,
        warnings=warnings or [],
    )
