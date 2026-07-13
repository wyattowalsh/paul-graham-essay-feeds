#!/usr/bin/env python3
"""Build and safely update a complete RSS 2.0 feed for Paul Graham's essays.

The script is dependency-free and targets Python 3.11+. Running it with no
arguments fetches https://paulgraham.com/articles.html, extracts the official
newest-to-oldest essay list, validates it against the previous manifest, and
atomically updates the RSS feed only when its logical contents change.

Examples
--------
Update from the live page::

    ./update_feed.py

Validate local artifacts without network access::

    ./update_feed.py --check

Set the feed's deployed URL so an Atom self-link can be emitted::

    RSS_SELF_URL=https://example.com/paul-graham-essays.xml ./update_feed.py

Generate from a saved HTML file::

    ./update_feed.py --source-file articles.html --force
"""

from __future__ import annotations

import argparse
import hashlib
import html as html_module
import json
import os
import shutil
import sys
import tempfile
import time
import unicodedata
import uuid
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from email.utils import format_datetime, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Final
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen

VERSION: Final = "2.0.0"
SOURCE_URL: Final = "https://paulgraham.com/articles.html"
CHANNEL_URL: Final = SOURCE_URL
RSS_SPEC_URL: Final = "https://www.rssboard.org/rss-specification"
DC_NS: Final = "http://purl.org/dc/elements/1.1/"
ATOM_NS: Final = "http://www.w3.org/2005/Atom"
MIN_BASELINE_ITEMS: Final = 233
MAX_SOURCE_BYTES: Final = 5 * 1024 * 1024
ALLOWED_HOSTS: Final = frozenset({"paulgraham.com", "sep.turbifycdn.com"})
EXCLUDED_INTERNAL_PATHS: Final = frozenset(
    {"/", "/index.html", "/articles.html", "/rss.html"}
)
PROTECTED_EXTERNAL_PATHS: Final = frozenset(
    {
        "/ty/cdn/paulgraham/acl1.txt",
        "/ty/cdn/paulgraham/acl2.txt",
    }
)
RETRYABLE_HTTP_CODES: Final = frozenset({408, 425, 429, 500, 502, 503, 504})

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = PROJECT_DIR / "paul-graham-essays.rss.xml"
DEFAULT_MANIFEST = PROJECT_DIR / "paul-graham-essays.items.json"
DEFAULT_REPORT = PROJECT_DIR / "validation.json"
DEFAULT_STATE = PROJECT_DIR / ".update-state.json"
DEFAULT_CHECKSUMS = PROJECT_DIR / "SHA256SUMS"

ET.register_namespace("dc", DC_NS)
ET.register_namespace("atom", ATOM_NS)


class FeedError(RuntimeError):
    """Raised when fetching, extraction, reconciliation, or validation fails."""


@dataclass(frozen=True, slots=True)
class Anchor:
    href: str
    title: str
    marked_as_essay: bool


@dataclass(frozen=True, slots=True)
class FeedItem:
    position: int
    title: str
    url: str
    guid: str
    guid_is_permalink: bool

    @property
    def identity(self) -> str:
        """Stable item identity used for reconciliation across source updates."""
        return self.guid


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    items: tuple[FeedItem, ...]
    mode: str
    anchor_count: int
    marked_anchor_count: int
    duplicate_count: int


@dataclass(frozen=True, slots=True)
class FetchResult:
    body: bytes | None
    final_url: str
    etag: str | None
    last_modified: str | None
    status: int
    not_modified: bool


@dataclass(frozen=True, slots=True)
class ChangeSet:
    added: tuple[str, ...]
    removed: tuple[str, ...]
    title_changed: tuple[str, ...]
    link_changed: tuple[str, ...]
    order_changed: bool

    @property
    def changed(self) -> bool:
        return bool(
            self.added
            or self.removed
            or self.title_changed
            or self.link_changed
            or self.order_changed
        )


class EssayAnchorParser(HTMLParser):
    """Collect visible anchors and recognize the site's essay-row marker image."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[Anchor] = []
        self._href: str | None = None
        self._parts: list[str] = []
        self._current_marked = False
        self._pending_essay_marker = False

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        tag = tag.lower()
        attr_map = {key.lower(): value for key, value in attrs}

        if tag == "img":
            src = attr_map.get("src") or ""
            if urlsplit(src).path.endswith("/the-reddits-2.gif"):
                self._pending_essay_marker = True
            return

        if tag != "a":
            return

        href = attr_map.get("href")
        if href is None:
            return

        self._href = href.strip()
        self._parts = []
        self._current_marked = self._pending_essay_marker
        self._pending_essay_marker = False

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._href is None:
            return

        title = normalize_text("".join(self._parts))
        self.anchors.append(
            Anchor(
                href=self._href,
                title=title,
                marked_as_essay=self._current_marked,
            )
        )
        self._href = None
        self._parts = []
        self._current_marked = False


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value)
    # XML 1.0 permits TAB, LF, CR, and the standard Unicode ranges below.
    # Removing forbidden controls prevents a future source-title anomaly from
    # producing XML that serializes but cannot be consumed by strict parsers.
    xml_safe = "".join(
        character
        for character in normalized
        if (
            character in "\t\n\r"
            or 0x20 <= ord(character) <= 0xD7FF
            or 0xE000 <= ord(character) <= 0xFFFD
            or 0x10000 <= ord(character) <= 0x10FFFF
        )
    )
    return " ".join(xml_safe.split())


def canonicalize_url(base_url: str, href: str) -> str:
    """Resolve, HTTPS-normalize, de-fragment, and clean a source URL."""
    parts = urlsplit(urljoin(base_url, html_module.unescape(href.strip())))
    if parts.scheme.lower() not in {"http", "https"}:
        raise FeedError(f"Unsupported URL scheme in {href!r}.")
    if parts.username or parts.password:
        raise FeedError(f"User-info is not permitted in item URL {href!r}.")

    host = (parts.hostname or "").lower().rstrip(".")
    if host == "www.paulgraham.com":
        host = "paulgraham.com"
    if host not in ALLOWED_HOSTS:
        raise FeedError(f"Unexpected item host {host!r} in {href!r}.")

    port = parts.port
    netloc = host if port is None else f"{host}:{port}"
    path = parts.path or "/"

    # Internal essay pages are canonical without query parameters. The two CDN
    # links retain their live cache-busting query, but empty/trailing parameters
    # are removed so `...?t=123&` becomes `...?t=123`.
    if host == "paulgraham.com":
        query = ""
    else:
        query = urlencode(parse_qsl(parts.query, keep_blank_values=False))

    return urlunsplit(("https", netloc, path, query, ""))


def is_content_candidate(url: str, title: str) -> bool:
    if not title:
        return False
    parts = urlsplit(url)
    if parts.hostname == "paulgraham.com":
        return parts.path not in EXCLUDED_INTERNAL_PATHS
    return (
        parts.hostname == "sep.turbifycdn.com"
        and parts.path in PROTECTED_EXTERNAL_PATHS
    )


def make_guid(url: str) -> tuple[str, bool]:
    parts = urlsplit(url)
    if parts.hostname == "paulgraham.com":
        return url, True

    stable_url = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    return f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, stable_url)}", False


def _anchors_to_items(
    anchors: Iterable[Anchor],
    *,
    base_url: str,
    deduplicate_by_last_occurrence: bool,
) -> tuple[list[FeedItem], int]:
    rows: list[tuple[str, str, str, bool]] = []
    for anchor in anchors:
        if not anchor.title:
            continue
        try:
            url = canonicalize_url(base_url, anchor.href)
        except FeedError:
            # Navigation or unrelated off-site anchors are not feed content.
            continue
        if not is_content_candidate(url, anchor.title):
            continue
        guid, is_permalink = make_guid(url)
        rows.append((anchor.title, url, guid, is_permalink))

    duplicate_count = 0
    if deduplicate_by_last_occurrence:
        last_index = {guid: index for index, (*_, guid, _) in enumerate(rows)}
        duplicate_count = len(rows) - len(last_index)
        rows = [row for index, row in enumerate(rows) if last_index[row[2]] == index]

    items = [
        FeedItem(
            position=index,
            title=title,
            url=url,
            guid=guid,
            guid_is_permalink=is_permalink,
        )
        for index, (title, url, guid, is_permalink) in enumerate(rows, start=1)
    ]
    return items, duplicate_count


def extract_items(
    source_html: str,
    *,
    base_url: str,
    min_items: int,
    require_protected_external: bool = True,
) -> ExtractionResult:
    parser = EssayAnchorParser()
    parser.feed(source_html)
    parser.close()

    marked = [anchor for anchor in parser.anchors if anchor.marked_as_essay]
    marked_items, marked_duplicates = _anchors_to_items(
        marked,
        base_url=base_url,
        deduplicate_by_last_occurrence=False,
    )

    if len(marked_items) >= min_items:
        items = marked_items
        mode = "essay-row-marker"
        duplicate_count = marked_duplicates
    else:
        # Safe fallback for a future visual redesign: collect all plausible
        # content anchors, keep their final occurrence (the main list rather
        # than the three recommendations), then let manifest reconciliation
        # reject removals, reordering, or non-prefix additions by default.
        items, duplicate_count = _anchors_to_items(
            parser.anchors,
            base_url=base_url,
            deduplicate_by_last_occurrence=True,
        )
        mode = "filtered-anchor-fallback"

    validate_extracted_items(
        items,
        min_items=min_items,
        require_protected_external=require_protected_external,
    )
    return ExtractionResult(
        items=tuple(items),
        mode=mode,
        anchor_count=len(parser.anchors),
        marked_anchor_count=len(marked),
        duplicate_count=duplicate_count,
    )


def validate_extracted_items(
    items: Sequence[FeedItem],
    *,
    min_items: int,
    require_protected_external: bool,
) -> None:
    if len(items) < min_items:
        raise FeedError(
            f"Extracted {len(items)} items, below the safety floor of {min_items}."
        )

    identities = [item.identity for item in items]
    duplicates = sorted(
        identity for identity, count in Counter(identities).items() if count > 1
    )
    if duplicates:
        raise FeedError(f"Duplicate stable item identities: {duplicates}")

    links = [item.url for item in items]
    duplicate_links = sorted(
        link for link, count in Counter(links).items() if count > 1
    )
    if duplicate_links:
        raise FeedError(f"Duplicate item links: {duplicate_links}")

    for item in items:
        if not item.title or item.title != normalize_text(item.title):
            raise FeedError(f"Unnormalized or empty title at position {item.position}.")
        parts = urlsplit(item.url)
        if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS:
            raise FeedError(f"Non-canonical item URL: {item.url}")
        if "paulgraham.com/https://" in item.url:
            raise FeedError(f"Malformed doubly-prefixed item URL: {item.url}")
        if item.guid_is_permalink and item.guid != item.url:
            raise FeedError(f"Permalink GUID does not equal item link: {item.url}")

    external_paths = {
        urlsplit(item.url).path
        for item in items
        if urlsplit(item.url).hostname == "sep.turbifycdn.com"
    }
    missing_external = sorted(PROTECTED_EXTERNAL_PATHS - external_paths)
    if require_protected_external and missing_external:
        raise FeedError(
            "The protected ANSI Common Lisp chapter links are missing: "
            + ", ".join(missing_external)
        )


def _manifest_items(data: dict[str, Any]) -> tuple[FeedItem, ...]:
    raw_items = data.get("items")
    if not isinstance(raw_items, list):
        raise FeedError("Manifest does not contain an items array.")

    items: list[FeedItem] = []
    for index, row in enumerate(raw_items, start=1):
        if not isinstance(row, dict):
            raise FeedError(f"Manifest item {index} is not an object.")
        title = normalize_text(str(row.get("title", "")))
        url = str(row.get("url", "")).strip()
        guid = str(row.get("guid", "")).strip()
        is_permalink = row.get("guid_is_permalink")
        if not guid:
            guid, derived_is_permalink = make_guid(url)
            if is_permalink is None:
                is_permalink = derived_is_permalink
        if is_permalink is None:
            is_permalink = urlsplit(url).hostname == "paulgraham.com"
        items.append(
            FeedItem(
                position=index,
                title=title,
                url=url,
                guid=guid,
                guid_is_permalink=bool(is_permalink),
            )
        )
    return tuple(items)


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FeedError(f"Required file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise FeedError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise FeedError(f"Expected a JSON object in {path}.")
    return data


def load_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return load_json(path)


def reconcile_items(
    previous: Sequence[FeedItem],
    current: Sequence[FeedItem],
    *,
    allow_removals: bool,
    allow_nonprefix_additions: bool,
) -> ChangeSet:
    old_by_id = {item.identity: item for item in previous}
    new_by_id = {item.identity: item for item in current}
    old_ids = [item.identity for item in previous]
    new_ids = [item.identity for item in current]

    removed = tuple(identity for identity in old_ids if identity not in new_by_id)
    added = tuple(identity for identity in new_ids if identity not in old_by_id)

    if removed and not allow_removals:
        removed_urls = [old_by_id[identity].url for identity in removed]
        raise FeedError(
            "Source reconciliation detected removed items. Refusing to overwrite "
            "without --allow-removals:\n  " + "\n  ".join(removed_urls)
        )

    common_old = [identity for identity in old_ids if identity in new_by_id]
    common_new = [identity for identity in new_ids if identity in old_by_id]
    order_changed = common_old != common_new
    if order_changed:
        raise FeedError(
            "Existing essay order changed unexpectedly. Refusing to overwrite."
        )

    if added and previous and not allow_nonprefix_additions:
        first_old_index = next(
            (index for index, identity in enumerate(new_ids) if identity in old_by_id),
            len(new_ids),
        )
        nonprefix = [
            identity
            for identity in new_ids[first_old_index:]
            if identity not in old_by_id
        ]
        if nonprefix:
            urls = [new_by_id[identity].url for identity in nonprefix]
            raise FeedError(
                "New links appeared inside or after the existing archive rather "
                "than as a newest-item prefix. Refusing to overwrite without "
                "--allow-nonprefix-additions:\n  " + "\n  ".join(urls)
            )

    title_changed = tuple(
        identity
        for identity in common_old
        if old_by_id[identity].title != new_by_id[identity].title
    )
    link_changed = tuple(
        identity
        for identity in common_old
        if old_by_id[identity].url != new_by_id[identity].url
    )

    return ChangeSet(
        added=added,
        removed=removed,
        title_changed=title_changed,
        link_changed=link_changed,
        order_changed=order_changed,
    )


def rfc822_utc(value: datetime) -> str:
    return format_datetime(value.astimezone(UTC), usegmt=True)


def build_feed(
    items: Sequence[FeedItem],
    *,
    last_build_date: datetime,
    self_url: str | None,
) -> bytes:
    root = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(root, "channel")

    metadata = (
        ("title", "Paul Graham: Essays"),
        ("link", CHANNEL_URL),
        (
            "description",
            "All essays and selected book chapters listed on Paul Graham's "
            "official essays index, ordered newest to oldest.",
        ),
        ("language", "en-US"),
        ("lastBuildDate", rfc822_utc(last_build_date)),
        ("category", "Essays"),
        ("generator", f"pg-essays-rss/{VERSION}"),
        ("docs", RSS_SPEC_URL),
        ("ttl", "1440"),
    )
    for tag, value in metadata:
        ET.SubElement(channel, tag).text = value

    if self_url:
        canonical_self = canonicalize_public_url(self_url, field="self URL")
        ET.SubElement(
            channel,
            f"{{{ATOM_NS}}}link",
            {
                "href": canonical_self,
                "rel": "self",
                "type": "application/rss+xml",
            },
        )

    for source_item in items:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = source_item.title
        ET.SubElement(item, "link").text = source_item.url
        ET.SubElement(
            item, "description"
        ).text = f"Read “{source_item.title}” by Paul Graham."
        ET.SubElement(item, f"{{{DC_NS}}}creator").text = "Paul Graham"
        ET.SubElement(
            item,
            "guid",
            {"isPermaLink": ("true" if source_item.guid_is_permalink else "false")},
        ).text = source_item.guid

    ET.indent(root, space="  ")
    xml_body = ET.tostring(
        root,
        encoding="utf-8",
        xml_declaration=False,
        short_empty_elements=True,
    )
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + xml_body + b"\n"


def canonicalize_public_url(value: str, *, field: str) -> str:
    parts = urlsplit(value.strip())
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise FeedError(f"{field} must be an absolute HTTP(S) URL: {value!r}")
    if parts.username or parts.password or parts.fragment:
        raise FeedError(f"{field} must not contain user-info or a fragment.")
    return urlunsplit(parts)


def parse_rfc822_date(value: str, *, field: str) -> datetime:
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError) as exc:
        raise FeedError(f"Invalid {field}: {value!r}") from exc
    if parsed is None or parsed.tzinfo is None:
        raise FeedError(f"{field} must include a timezone: {value!r}")
    return parsed


def validate_feed_bytes(
    xml_bytes: bytes,
    *,
    expected_items: Sequence[FeedItem],
    min_items: int,
    expected_self_url: str | None,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise FeedError(f"Generated XML is not well-formed: {exc}") from exc

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
    last_build_date = None
    self_links: list[ET.Element] = []

    if channel is not None:
        for tag in ("title", "link", "description"):
            nodes = channel.findall(tag)
            if len(nodes) != 1 or not normalize_text(nodes[0].text or ""):
                errors.append(f"Channel requires exactly one non-empty {tag} element.")

        for tag in (
            "language",
            "lastBuildDate",
            "category",
            "generator",
            "docs",
            "ttl",
        ):
            nodes = channel.findall(tag)
            if len(nodes) != 1 or not normalize_text(nodes[0].text or ""):
                errors.append(
                    f"Generated channel metadata requires exactly one non-empty {tag}."
                )

        children = list(channel)
        first_item = next(
            (index for index, child in enumerate(children) if child.tag == "item"),
            len(children),
        )
        if any(child.tag != "item" for child in children[first_item:]):
            errors.append("All channel metadata must precede item elements.")

        channel_link = normalize_text(channel.findtext("link") or "")
        try:
            canonicalize_public_url(channel_link, field="channel link")
        except FeedError as exc:
            errors.append(str(exc))
        if channel_link != CHANNEL_URL:
            errors.append(
                f"Channel link must be the official essays index: {CHANNEL_URL}"
            )

        language = normalize_text(channel.findtext("language") or "")
        if language != "en-US":
            errors.append("Channel language must be exactly 'en-US'.")

        generator = normalize_text(channel.findtext("generator") or "")
        if generator != f"pg-essays-rss/{VERSION}":
            errors.append(f"Channel generator must be pg-essays-rss/{VERSION}.")

        docs = normalize_text(channel.findtext("docs") or "")
        if docs != RSS_SPEC_URL:
            errors.append("Channel docs must reference the current RSS specification.")

        category = normalize_text(channel.findtext("category") or "")
        if category != "Essays":
            errors.append("Channel category must be exactly 'Essays'.")

        ttl = normalize_text(channel.findtext("ttl") or "")
        if not ttl.isdigit() or int(ttl) <= 0:
            errors.append("Channel ttl must be a positive integer number of minutes.")

        last_build_text = normalize_text(channel.findtext("lastBuildDate") or "")
        if not last_build_text:
            errors.append("Channel must include lastBuildDate.")
        else:
            try:
                last_build_date = parse_rfc822_date(
                    last_build_text,
                    field="lastBuildDate",
                )
            except FeedError as exc:
                errors.append(str(exc))

        self_links = channel.findall(f"{{{ATOM_NS}}}link")
        if expected_self_url:
            if len(self_links) != 1:
                errors.append("Expected exactly one atom:link rel='self'.")
            else:
                self_link = self_links[0]
                if self_link.attrib.get("rel") != "self":
                    errors.append("atom:link must have rel='self'.")
                if self_link.attrib.get("type") != "application/rss+xml":
                    errors.append("atom:link must have type='application/rss+xml'.")
                if self_link.attrib.get("href") != canonicalize_public_url(
                    expected_self_url,
                    field="self URL",
                ):
                    errors.append("atom:link href does not match configured self URL.")
        elif self_links:
            errors.append(
                "Feed contains atom:link rel='self' but no deployment URL was configured."
            )

        for index, item in enumerate(channel.findall("item"), start=1):
            titles = item.findall("title")
            links = item.findall("link")
            descriptions = item.findall("description")
            creators = item.findall(f"{{{DC_NS}}}creator")
            guids = item.findall("guid")

            if len(titles) != 1:
                errors.append(f"Item {index} must contain exactly one title.")
            if len(links) != 1:
                errors.append(f"Item {index} must contain exactly one link.")
            if len(descriptions) != 1:
                errors.append(f"Item {index} must contain exactly one description.")
            if len(creators) != 1:
                errors.append(f"Item {index} must contain exactly one dc:creator.")
            if len(guids) != 1:
                errors.append(f"Item {index} must contain exactly one guid.")

            title = normalize_text(titles[0].text or "") if titles else ""
            link = normalize_text(links[0].text or "") if links else ""
            guid = normalize_text(guids[0].text or "") if guids else ""
            is_permalink_text = (
                guids[0].attrib.get("isPermaLink", "true").lower() if guids else ""
            )
            is_permalink = is_permalink_text == "true"

            if not title:
                errors.append(f"Item {index} title is empty.")
            if descriptions:
                if not normalize_text(descriptions[0].text or ""):
                    errors.append(f"Item {index} description is empty.")
                if list(descriptions[0]):
                    errors.append(
                        f"Item {index} description must contain character data, "
                        "not nested XML elements."
                    )
            if creators and normalize_text(creators[0].text or "") != "Paul Graham":
                errors.append(f"Item {index} dc:creator must be Paul Graham.")
            try:
                parts = urlsplit(link)
                if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS:
                    raise FeedError(f"Non-canonical item URL: {link}")
            except (ValueError, FeedError) as exc:
                errors.append(f"Item {index}: {exc}")
            if not guid:
                errors.append(f"Item {index} guid is empty.")
            if is_permalink_text not in {"true", "false"}:
                errors.append(f"Item {index} guid@isPermaLink must be true or false.")
            if is_permalink and guid != link:
                errors.append(f"Item {index} permalink GUID must equal its item link.")

            parsed_rows.append((title, link, guid, is_permalink))

    expected_rows = [
        (item.title, item.url, item.guid, item.guid_is_permalink)
        for item in expected_items
    ]
    if parsed_rows != expected_rows:
        errors.append(
            "RSS item titles, links, GUIDs, or ordering do not exactly match "
            "the normalized source manifest."
        )

    if len(parsed_rows) < min_items:
        errors.append(
            f"Feed has {len(parsed_rows)} items, below safety floor {min_items}."
        )

    links = [row[1] for row in parsed_rows]
    guids = [row[2] for row in parsed_rows]
    duplicate_links = sorted(
        value for value, count in Counter(links).items() if count > 1
    )
    duplicate_guids = sorted(
        value for value, count in Counter(guids).items() if count > 1
    )
    if duplicate_links:
        errors.append(f"Duplicate item links: {duplicate_links}")
    if duplicate_guids:
        errors.append(f"Duplicate item GUIDs: {duplicate_guids}")
    if any("paulgraham.com/https://" in link for link in links):
        errors.append("At least one malformed doubly-prefixed URL remains.")

    result = {
        "valid": not errors,
        "rss_version": root.attrib.get("version"),
        "item_count": len(parsed_rows),
        "unique_link_count": len(set(links)),
        "unique_guid_count": len(set(guids)),
        "first_item": (
            {"title": parsed_rows[0][0], "url": parsed_rows[0][1]}
            if parsed_rows
            else None
        ),
        "last_item": (
            {"title": parsed_rows[-1][0], "url": parsed_rows[-1][1]}
            if parsed_rows
            else None
        ),
        "last_build_date": (
            last_build_date.astimezone(UTC).isoformat()
            if last_build_date
            else None
        ),
        "self_link": self_links[0].attrib.get("href") if self_links else None,
        "checks": {
            "xml_well_formed": True,
            "rss_version_2_0": root.attrib.get("version") == "2.0",
            "single_channel": len(channels) == 1,
            "required_channel_elements": not any(
                error.startswith("Channel requires") for error in errors
            ),
            "canonical_channel_metadata": not any(
                error.startswith("Channel ")
                and not error.startswith("Channel requires")
                for error in errors
            ),
            "metadata_before_items": "All channel metadata" not in errors,
            "source_alignment_exact": parsed_rows == expected_rows,
            "absolute_https_item_links": not any(
                "Non-canonical item URL" in error for error in errors
            ),
            "unique_links": not duplicate_links,
            "unique_guids": not duplicate_guids,
            "stable_permalink_guids": not any(
                "permalink GUID" in error for error in errors
            ),
            "no_double_prefixed_urls": not any(
                "doubly-prefixed" in error for error in errors
            ),
            "minimum_item_floor_met": len(parsed_rows) >= min_items,
            "self_link_configuration_consistent": not any(
                "atom:link" in error for error in errors
            ),
        },
        "errors": errors,
        "warnings": warnings,
    }
    if errors:
        raise FeedError("Feed validation failed:\n- " + "\n- ".join(errors))
    return result


def read_limited(response: Any, *, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(min(64 * 1024, max_bytes + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > max_bytes:
            raise FeedError(
                f"Source response exceeded maximum size of {max_bytes} bytes."
            )
    return b"".join(chunks)


def fetch_source(
    url: str,
    *,
    timeout: float,
    retries: int,
    max_bytes: int,
    state: dict[str, Any],
    conditional: bool,
) -> FetchResult:
    headers = {
        "User-Agent": f"pg-essays-rss/{VERSION} (+{CHANNEL_URL})",
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
        "Accept-Encoding": "identity",
        "Cache-Control": "no-cache",
    }
    if conditional:
        etag = state.get("etag")
        last_modified = state.get("last_modified")
        if isinstance(etag, str) and etag:
            headers["If-None-Match"] = etag
        if isinstance(last_modified, str) and last_modified:
            headers["If-Modified-Since"] = last_modified

    for attempt in range(retries + 1):
        request = Request(url, headers=headers)
        try:
            with urlopen(request, timeout=timeout) as response:
                body = read_limited(response, max_bytes=max_bytes)
                content_type = response.headers.get_content_type()
                if content_type not in {
                    "text/html",
                    "application/xhtml+xml",
                    "text/plain",
                }:
                    raise FeedError(
                        f"Unexpected source content type: {content_type!r}."
                    )
                return FetchResult(
                    body=body,
                    final_url=response.geturl(),
                    etag=response.headers.get("ETag"),
                    last_modified=response.headers.get("Last-Modified"),
                    status=getattr(response, "status", 200),
                    not_modified=False,
                )
        except HTTPError as exc:
            if exc.code == 304:
                return FetchResult(
                    body=None,
                    final_url=url,
                    etag=state.get("etag"),
                    last_modified=state.get("last_modified"),
                    status=304,
                    not_modified=True,
                )
            if exc.code not in RETRYABLE_HTTP_CODES or attempt >= retries:
                raise FeedError(f"HTTP {exc.code} while fetching {url}.") from exc
        except (URLError, TimeoutError, OSError) as exc:
            if attempt >= retries:
                raise FeedError(f"Unable to fetch {url}: {exc}") from exc

        time.sleep(min(2**attempt, 8))

    raise AssertionError("retry loop exhausted unexpectedly")


def decode_source(body: bytes) -> str:
    if body.startswith(b"\xef\xbb\xbf"):
        return body.decode("utf-8-sig")
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        # The index is effectively ASCII, but Latin-1 is a safe lossless fallback
        # for older HTML that omits a charset declaration.
        return body.decode("latin-1")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def atomic_write(path: Path, data: bytes, *, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if mode is None:
        mode = (path.stat().st_mode & 0o777) if path.exists() else 0o644
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temp_path.chmod(mode)
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    atomic_write(
        path,
        (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
    )


def backup_file(path: Path) -> None:
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak"))


def logical_manifest_signature(
    *,
    items: Sequence[FeedItem],
    self_url: str | None,
) -> dict[str, Any]:
    return {
        "channel_url": CHANNEL_URL,
        "self_url": self_url,
        "generator_version": VERSION,
        "items": [
            {
                "title": item.title,
                "url": item.url,
                "guid": item.guid,
                "guid_is_permalink": item.guid_is_permalink,
            }
            for item in items
        ],
    }


def build_manifest(
    *,
    items: Sequence[FeedItem],
    self_url: str | None,
    source_sha256: str,
    source_url: str,
    extraction: ExtractionResult,
    updated_at: datetime,
) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "source_url": source_url,
        "channel_url": CHANNEL_URL,
        "self_url": self_url,
        "generator": f"pg-essays-rss/{VERSION}",
        "updated_at": updated_at.isoformat(),
        "source_sha256": source_sha256,
        "extraction": {
            "mode": extraction.mode,
            "anchor_count": extraction.anchor_count,
            "marked_anchor_count": extraction.marked_anchor_count,
            "duplicate_count": extraction.duplicate_count,
        },
        "item_count": len(items),
        "items": [asdict(item) for item in items],
        "logical_signature_sha256": sha256_bytes(
            json.dumps(
                logical_manifest_signature(items=items, self_url=self_url),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ),
    }


def display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_DIR))
    except ValueError:
        return str(path)


def checksum_targets(
    *,
    output: Path,
    manifest: Path,
    report: Path,
) -> list[Path]:
    return [
        output,
        manifest,
        report,
        Path(__file__),
        PROJECT_DIR / "README.md",
        PROJECT_DIR / "update.sh",
        PROJECT_DIR / "update.ps1",
        PROJECT_DIR / "test_update_feed.py",
        PROJECT_DIR / "pyproject.toml",
        PROJECT_DIR / "uv.lock",
        PROJECT_DIR / ".gitignore",
    ]


def build_report(
    *,
    status: str,
    validation: dict[str, Any],
    source_url: str,
    source_sha256: str | None,
    extraction: ExtractionResult | None,
    changes: ChangeSet | None,
    feed_path: Path,
) -> dict[str, Any]:
    return {
        "valid": validation["valid"],
        "status": status,
        "validated_at": utc_now().isoformat(),
        "source_url": source_url,
        "source_sha256": source_sha256,
        "feed_path": display_path(feed_path),
        "feed_sha256": sha256_bytes(feed_path.read_bytes())
        if feed_path.exists()
        else None,
        "extraction": (
            {
                "mode": extraction.mode,
                "anchor_count": extraction.anchor_count,
                "marked_anchor_count": extraction.marked_anchor_count,
                "duplicate_count": extraction.duplicate_count,
            }
            if extraction
            else None
        ),
        "changes": (
            {
                "added": list(changes.added),
                "removed": list(changes.removed),
                "title_changed": list(changes.title_changed),
                "link_changed": list(changes.link_changed),
                "order_changed": changes.order_changed,
            }
            if changes
            else None
        ),
        **validation,
    }


def write_checksums(path: Path, files: Sequence[Path]) -> None:
    rows: list[str] = []
    for file_path in sorted(files, key=lambda value: value.name):
        if file_path.exists() and file_path != path:
            rows.append(f"{sha256_bytes(file_path.read_bytes())}  {file_path.name}")
    atomic_write(path, ("\n".join(rows) + "\n").encode("utf-8"))


def resolve_self_url(cli_value: str | None) -> str | None:
    value = cli_value if cli_value is not None else os.getenv("RSS_SELF_URL")
    if value is None or not value.strip():
        return None
    return canonicalize_public_url(value, field="self URL")


def existing_manifest_signature(data: dict[str, Any]) -> dict[str, Any] | None:
    try:
        items = _manifest_items(data)
    except FeedError:
        return None
    self_url = data.get("self_url")
    return logical_manifest_signature(
        items=items,
        self_url=self_url if isinstance(self_url, str) and self_url else None,
    )


def validate_existing(
    *,
    output: Path,
    manifest: Path,
    report: Path,
    min_items: int,
    self_url: str | None,
    write_report: bool,
) -> dict[str, Any]:
    manifest_data = load_json(manifest)
    items = _manifest_items(manifest_data)
    validation = validate_feed_bytes(
        output.read_bytes(),
        expected_items=items,
        min_items=min_items,
        expected_self_url=self_url,
    )
    result = build_report(
        status="checked",
        validation=validation,
        source_url=str(manifest_data.get("source_url", SOURCE_URL)),
        source_sha256=(
            str(manifest_data.get("source_sha256"))
            if manifest_data.get("source_sha256")
            else None
        ),
        extraction=None,
        changes=None,
        feed_path=output,
    )
    if isinstance(manifest_data.get("extraction"), dict):
        result["extraction"] = manifest_data["extraction"]
    if write_report:
        atomic_write_json(report, result)
    return result


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Safely generate and update a complete RSS 2.0 feed from Paul "
            "Graham's official essays index."
        )
    )
    parser.add_argument("--version", action="version", version=VERSION)
    parser.add_argument("--source-url", default=SOURCE_URL)
    parser.add_argument(
        "--source-file",
        type=Path,
        help="Read HTML from a local file instead of fetching the live page.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--checksums", type=Path, default=DEFAULT_CHECKSUMS)
    parser.add_argument(
        "--self-url",
        help="Public URL of this RSS file; defaults to RSS_SELF_URL.",
    )
    parser.add_argument("--min-items", type=int, default=MIN_BASELINE_ITEMS)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--retries", type=int, default=4)
    parser.add_argument("--max-source-bytes", type=int, default=MAX_SOURCE_BYTES)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate the existing feed and manifest without fetching.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore conditional HTTP metadata and rewrite even if unchanged.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-removals", action="store_true")
    parser.add_argument("--allow-nonprefix-additions", action="store_true")
    parser.add_argument("--no-backup", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    if args.min_items < 1:
        parser.error("--min-items must be at least 1.")
    if args.retries < 0:
        parser.error("--retries must be non-negative.")
    if args.timeout <= 0:
        parser.error("--timeout must be positive.")
    if args.max_source_bytes < 1024:
        parser.error("--max-source-bytes is implausibly small.")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    self_url = resolve_self_url(args.self_url)

    try:
        if args.check:
            result = validate_existing(
                output=args.output,
                manifest=args.manifest,
                report=args.report,
                min_items=args.min_items,
                self_url=self_url,
                write_report=not args.dry_run,
            )
            if not args.dry_run:
                write_checksums(
                    args.checksums,
                    checksum_targets(
                        output=args.output,
                        manifest=args.manifest,
                        report=args.report,
                    ),
                )
            if not args.quiet:
                print(
                    f"VALID: {result['item_count']} items; "
                    f"{result['unique_guid_count']} unique GUIDs."
                )
            return 0

        state = load_optional_json(args.state)
        fetch_result: FetchResult | None = None

        if args.source_file:
            body = args.source_file.read_bytes()
            if len(body) > args.max_source_bytes:
                raise FeedError(f"Source file exceeds {args.max_source_bytes} bytes.")
            source_url = args.source_url
        else:
            fetch_result = fetch_source(
                args.source_url,
                timeout=args.timeout,
                retries=args.retries,
                max_bytes=args.max_source_bytes,
                state=state,
                conditional=(
                    not args.force and args.output.exists() and args.manifest.exists()
                ),
            )
            if fetch_result.not_modified:
                try:
                    result = validate_existing(
                        output=args.output,
                        manifest=args.manifest,
                        report=args.report,
                        min_items=args.min_items,
                        self_url=self_url,
                        write_report=not args.dry_run,
                    )
                except FeedError:
                    # A 304 is unusable when local artifacts are invalid. Retry
                    # once without validators to repair them from the full body.
                    fetch_result = fetch_source(
                        args.source_url,
                        timeout=args.timeout,
                        retries=args.retries,
                        max_bytes=args.max_source_bytes,
                        state={},
                        conditional=False,
                    )
                else:
                    if not args.dry_run:
                        state.update(
                            {
                                "schema_version": 1,
                                "source_url": args.source_url,
                                "etag": fetch_result.etag,
                                "last_modified": fetch_result.last_modified,
                                "last_checked_at": utc_now().isoformat(),
                            }
                        )
                        atomic_write_json(args.state, state)
                        write_checksums(
                            args.checksums,
                            checksum_targets(
                                output=args.output,
                                manifest=args.manifest,
                                report=args.report,
                            ),
                        )
                    if not args.quiet:
                        print(
                            f"UNCHANGED (HTTP 304): {result['item_count']} valid items."
                        )
                    return 0

            if fetch_result.body is None:
                raise FeedError("Source fetch returned no body.")
            body = fetch_result.body
            source_url = fetch_result.final_url

        source_sha256 = sha256_bytes(body)
        extraction = extract_items(
            decode_source(body),
            base_url=args.source_url,
            min_items=args.min_items,
            require_protected_external=not args.allow_removals,
        )
        current_items = extraction.items

        previous_manifest = load_optional_json(args.manifest)
        previous_items = (
            _manifest_items(previous_manifest) if previous_manifest else tuple()
        )
        changes = reconcile_items(
            previous_items,
            current_items,
            allow_removals=args.allow_removals,
            allow_nonprefix_additions=args.allow_nonprefix_additions,
        )

        new_signature = logical_manifest_signature(
            items=current_items,
            self_url=self_url,
        )
        old_signature = (
            existing_manifest_signature(previous_manifest)
            if previous_manifest
            else None
        )
        logical_change = old_signature != new_signature

        if not logical_change and not args.force:
            validation = validate_feed_bytes(
                args.output.read_bytes(),
                expected_items=current_items,
                min_items=args.min_items,
                expected_self_url=self_url,
            )
            report = build_report(
                status="unchanged",
                validation=validation,
                source_url=source_url,
                source_sha256=source_sha256,
                extraction=extraction,
                changes=changes,
                feed_path=args.output,
            )
            if not args.dry_run:
                atomic_write_json(args.report, report)
                state_payload = {
                    "schema_version": 1,
                    "source_url": args.source_url,
                    "etag": fetch_result.etag if fetch_result else None,
                    "last_modified": (
                        fetch_result.last_modified if fetch_result else None
                    ),
                    "source_sha256": source_sha256,
                    "last_checked_at": utc_now().isoformat(),
                }
                atomic_write_json(args.state, state_payload)
                write_checksums(
                    args.checksums,
                    checksum_targets(
                        output=args.output,
                        manifest=args.manifest,
                        report=args.report,
                    ),
                )
            if not args.quiet:
                print(
                    f"UNCHANGED: {validation['item_count']} valid items; no feed rewrite."
                )
            return 0

        updated_at = utc_now()
        xml_bytes = build_feed(
            current_items,
            last_build_date=updated_at,
            self_url=self_url,
        )
        validation = validate_feed_bytes(
            xml_bytes,
            expected_items=current_items,
            min_items=args.min_items,
            expected_self_url=self_url,
        )
        manifest_payload = build_manifest(
            items=current_items,
            self_url=self_url,
            source_sha256=source_sha256,
            source_url=source_url,
            extraction=extraction,
            updated_at=updated_at,
        )

        if args.dry_run:
            if not args.quiet:
                print(
                    f"DRY RUN: would write {len(current_items)} items "
                    f"({len(changes.added)} added, {len(changes.removed)} removed)."
                )
            return 0

        if not args.no_backup:
            backup_file(args.output)
            backup_file(args.manifest)

        atomic_write(args.output, xml_bytes)
        atomic_write_json(args.manifest, manifest_payload)
        report_payload = build_report(
            status="updated",
            validation=validation,
            source_url=source_url,
            source_sha256=source_sha256,
            extraction=extraction,
            changes=changes,
            feed_path=args.output,
        )
        atomic_write_json(args.report, report_payload)

        state_payload = {
            "schema_version": 1,
            "source_url": args.source_url,
            "etag": fetch_result.etag if fetch_result else None,
            "last_modified": fetch_result.last_modified if fetch_result else None,
            "source_sha256": source_sha256,
            "last_checked_at": updated_at.isoformat(),
        }
        atomic_write_json(args.state, state_payload)
        write_checksums(
            args.checksums,
            checksum_targets(
                output=args.output,
                manifest=args.manifest,
                report=args.report,
            ),
        )

        if not args.quiet:
            print(
                f"UPDATED: wrote {len(current_items)} items to {args.output.name}; "
                f"validation passed."
            )
        return 0

    except (FeedError, FileNotFoundError, PermissionError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
