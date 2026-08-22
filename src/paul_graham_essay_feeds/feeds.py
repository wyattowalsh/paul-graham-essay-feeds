"""Render and write RSS/Atom/JSON feed artifacts from ``FeedSnapshot``.

Note: this module is named ``feeds`` (package code); generated files live in
the repository ``feeds/`` directory.

Flat layout (six files)::

    feeds/rss.xml / atom.xml / feed.json          — enriched
    feeds/rss.simple.xml / atom.simple.xml / feed.simple.json — simple
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Final, Literal

from loguru import logger

from paul_graham_essay_feeds.catalog import atomic_write_bytes
from paul_graham_essay_feeds.models import (
    ATOM_NS,
    AUTHOR,
    AUTHOR_URL,
    DC_NS,
    FEED_DESCRIPTION,
    FEED_ID,
    FEED_ID_SIMPLE,
    FEED_SUMMARY_CHARS,
    FEED_TITLE,
    JSON_FEED_VERSION,
    NULL_REPORTER,
    SOURCE_URL,
    Catalog,
    FeedEntrySnapshot,
    FeedError,
    FeedSnapshot,
    ProgressReporter,
    blurb,
    rfc822,
    rfc3339,
    truncate_text,
)

SummaryMode = Literal["enriched", "title_only"]
DEFAULT_FEEDS_RELATIVE_DIR = "feeds"

ENRICHED_FEED_NAMES: Final[dict[str, str]] = {
    "rss": "rss.xml",
    "atom": "atom.xml",
    "json": "feed.json",
}
SIMPLE_FEED_NAMES: Final[dict[str, str]] = {
    "rss": "rss.simple.xml",
    "atom": "atom.simple.xml",
    "json": "feed.simple.json",
}


def feed_self_url(json_feed_url: str, *, kind: Literal["rss", "atom", "json"]) -> str:
    """Derive a self-link URL from the JSON Feed public URL without brittle replaces.

    Accepts either enriched (``…/feed.json``) or simple (``…/feed.simple.json``)
    JSON Feed self URLs produced by :func:`catalog_to_feed_snapshot`.
    """
    base = json_feed_url.rstrip("/")
    simple = base.endswith("/feed.simple.json") or base.endswith("feed.simple.json")
    if kind == "json":
        return base
    if simple:
        # …/feed.simple.json → …/rss.simple.xml | …/atom.simple.xml
        if base.endswith("feed.simple.json"):
            stem = base[: -len("feed.simple.json")]
            return f"{stem}{kind}.simple.xml"
        return base
    if base.endswith("feed.json"):
        stem = base[: -len("feed.json")]
        return f"{stem}{kind}.xml"
    return base


def catalog_to_feed_snapshot(
    catalog: Catalog,
    *,
    generator: str,
    public_base_url: str | None = None,
    index_hash: str | None = None,
    index_fingerprint: str | None = None,
    summary_mode: SummaryMode = "enriched",
) -> FeedSnapshot:
    """Project catalog ``entry_order`` into an immutable feed snapshot.

    Rules:

    - Every ``entry_order`` id must exist in ``entries`` (fail closed).
    - ``summary_mode="enriched"``: catalog summary when present, else title blurb.
    - ``summary_mode="title_only"``: always a title blurb (ignore catalog summary).
    - ``observed_updated_at`` = entry value, else ``first_seen_at``; entries
      missing both are skipped (reconcile should set observation times).
    - ``logical_updated_at`` = max item ``observed_updated_at``, else
      ``catalog.index.last_success_at`` (never the attempt clock).
    - Never invents 1970-01-01 observation times.
    """
    items: list[FeedEntrySnapshot] = []
    for stable_id in catalog.entry_order:
        entry = catalog.entries.get(stable_id)
        if entry is None:
            raise FeedError(f"Catalog entry_order references missing entry: {stable_id!r}")

        observed = entry.observed_updated_at or entry.first_seen_at
        if observed is None:
            # H-17: fail closed — never silently omit catalog entries from feeds.
            raise FeedError(
                f"Catalog entry {stable_id!r} lacks observed_updated_at/first_seen_at "
                "required for feed projection"
            )

        if summary_mode == "title_only":
            summary = _entry_summary(None, entry.title)
        else:
            summary = _entry_summary(entry.summary, entry.title)
        items.append(
            FeedEntrySnapshot(
                id=entry.stable_id,
                url=entry.url,
                title=entry.title,
                summary=summary,
                observed_updated_at=observed,
                published_at=entry.published_at,
            )
        )

    if items:
        logical_updated_at = max(item.observed_updated_at for item in items)
    else:
        # FeedSnapshot requires ≥1 item; index last_success is the only
        # remaining candidate clock (never invent wall-clock or 1970).
        logical_updated_at = catalog.index.last_success_at

    if not items or logical_updated_at is None:
        raise FeedError("Catalog has no entries with observation timestamps for feed projection")

    base = public_base_url.strip() if public_base_url else None
    if not base:
        base = None
    variant: Literal["enriched", "simple"] = (
        "simple" if summary_mode == "title_only" else "enriched"
    )
    json_name = SIMPLE_FEED_NAMES["json"] if variant == "simple" else ENRICHED_FEED_NAMES["json"]
    feed_url = f"{base.rstrip('/')}/{json_name}" if base is not None else None

    return FeedSnapshot(
        logical_updated_at=logical_updated_at,
        generator=generator,
        feed_url=feed_url,
        public_base_url=base,
        variant=variant,
        index_hash=index_hash,
        index_fingerprint=index_fingerprint,
        items=items,
    )


def render_snapshot_feeds(snapshot: FeedSnapshot) -> tuple[bytes, bytes, bytes]:
    """Render RSS, Atom, and JSON Feed bytes from ``snapshot``.

    Returns
    -------
    tuple[bytes, bytes, bytes]
        ``(rss, atom, json_feed)`` artifact bytes.
    """
    return (
        render_rss(snapshot),
        render_atom(snapshot),
        render_json(snapshot),
    )


def render_rss(snapshot: FeedSnapshot) -> bytes:
    """RSS 2.0: title, link, guid, short description — no full essay body."""
    ET.register_namespace("dc", DC_NS)
    ET.register_namespace("atom", ATOM_NS)
    root = ET.Element("rss", {"version": "2.0"})
    ch = ET.SubElement(root, "channel")
    for tag, val in (
        ("title", FEED_TITLE),
        ("link", SOURCE_URL),
        ("description", FEED_DESCRIPTION),
        ("language", "en-US"),
        ("lastBuildDate", rfc822(snapshot.logical_updated_at)),
        ("category", "Essays"),
        ("generator", snapshot.generator),
        ("docs", "https://www.rssboard.org/rss-specification"),
        ("ttl", "1440"),
    ):
        ET.SubElement(ch, tag).text = val

    if snapshot.feed_url is not None:
        ET.SubElement(
            ch,
            f"{{{ATOM_NS}}}link",
            {
                "rel": "self",
                "type": "application/rss+xml",
                "href": feed_self_url(snapshot.feed_url, kind="rss"),
            },
        )

    for entry in snapshot.items:
        item = ET.SubElement(ch, "item")
        ET.SubElement(item, "title").text = entry.title
        ET.SubElement(item, "link").text = entry.url
        ET.SubElement(item, "description").text = entry.summary
        ET.SubElement(item, f"{{{DC_NS}}}creator").text = AUTHOR
        ET.SubElement(item, "category").text = "Essays"
        is_permalink = _is_permalink(entry)
        guid_text = entry.url if is_permalink else entry.id
        ET.SubElement(
            item,
            "guid",
            {"isPermaLink": "true" if is_permalink else "false"},
        ).text = guid_text
        if entry.published_at is not None:
            ET.SubElement(item, "pubDate").text = rfc822(entry.published_at)

    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="utf-8", xml_declaration=False)
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + body + b"\n"


def render_atom(snapshot: FeedSnapshot) -> bytes:
    """Atom 1.0: title, link, id, summary — no full essay body."""
    feed = ET.Element(
        "feed",
        {
            "xmlns": ATOM_NS,
            "{http://www.w3.org/XML/1998/namespace}lang": "en",
        },
    )
    ET.SubElement(feed, "title").text = FEED_TITLE
    # Distinct Atom feed IDs for simple vs enriched subscriptions (H-15 / RV-R-002).
    atom_feed_id = FEED_ID_SIMPLE if snapshot.variant == "simple" else FEED_ID
    ET.SubElement(feed, "id").text = atom_feed_id
    ET.SubElement(feed, "updated").text = rfc3339(snapshot.logical_updated_at)
    ET.SubElement(feed, "subtitle").text = FEED_DESCRIPTION
    author = ET.SubElement(feed, "author")
    ET.SubElement(author, "name").text = AUTHOR
    ET.SubElement(author, "uri").text = AUTHOR_URL
    ET.SubElement(
        feed,
        "link",
        {"rel": "alternate", "type": "text/html", "href": SOURCE_URL},
    )
    ET.SubElement(feed, "generator").text = snapshot.generator

    if snapshot.feed_url is not None:
        self_href = feed_self_url(snapshot.feed_url, kind="atom")
        ET.SubElement(
            feed,
            "link",
            {
                "rel": "self",
                "type": "application/atom+xml",
                "href": self_href,
            },
        )

    for entry in snapshot.items:
        el = ET.SubElement(feed, "entry")
        ET.SubElement(el, "title").text = entry.title
        ET.SubElement(el, "id").text = entry.id
        ET.SubElement(el, "updated").text = rfc3339(entry.observed_updated_at)
        ET.SubElement(el, "summary", {"type": "text"}).text = entry.summary
        ET.SubElement(el, "link", {"rel": "alternate", "href": entry.url})
        if entry.published_at is not None:
            ET.SubElement(el, "published").text = rfc3339(entry.published_at)

    ET.indent(feed, space="  ")
    body = ET.tostring(feed, encoding="utf-8", xml_declaration=False)
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + body + b"\n"


def render_json(snapshot: FeedSnapshot) -> bytes:
    """JSON Feed 1.1: short summary/content_text — never full essay bodies."""
    items = []
    for entry in snapshot.items:
        item: dict = {
            "id": entry.id,
            "url": entry.url,
            "title": entry.title,
            "summary": entry.summary,
            "content_text": entry.summary,
            "date_modified": rfc3339(entry.observed_updated_at),
        }
        if entry.published_at is not None:
            item["date_published"] = rfc3339(entry.published_at)
        items.append(item)

    payload: dict = {
        "version": JSON_FEED_VERSION,
        "title": FEED_TITLE,
        "home_page_url": SOURCE_URL,
        "description": FEED_DESCRIPTION,
        "authors": [{"name": AUTHOR, "url": AUTHOR_URL}],
        "language": "en-US",
        "items": items,
    }
    if snapshot.feed_url is not None:
        payload["feed_url"] = snapshot.feed_url

    meta: dict = {
        "generator": snapshot.generator,
        "item_count": len(items),
        "logical_updated_at": rfc3339(snapshot.logical_updated_at),
    }
    if snapshot.index_hash is not None:
        meta["index_hash"] = snapshot.index_hash
    if snapshot.index_fingerprint is not None:
        meta["index_fingerprint"] = snapshot.index_fingerprint
    payload["_pg_essay_feeds"] = meta
    return (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def feed_paths(
    root: Path,
    *,
    relative_dir: str = DEFAULT_FEEDS_RELATIVE_DIR,
    kind: Literal["enriched", "simple"] = "enriched",
) -> dict[str, Path]:
    """Return rss/atom/json paths for the enriched or simple feed set."""
    feeds_dir = root / relative_dir
    names = SIMPLE_FEED_NAMES if kind == "simple" else ENRICHED_FEED_NAMES
    return {
        "rss": feeds_dir / names["rss"],
        "atom": feeds_dir / names["atom"],
        "json": feeds_dir / names["json"],
    }


def all_feed_paths(
    root: Path,
    *,
    relative_dir: str = DEFAULT_FEEDS_RELATIVE_DIR,
) -> dict[str, Path]:
    """All six flat feed artifact paths under ``feeds/``."""
    enriched = feed_paths(root, relative_dir=relative_dir, kind="enriched")
    simple = feed_paths(root, relative_dir=relative_dir, kind="simple")
    return {
        "rss": enriched["rss"],
        "atom": enriched["atom"],
        "json": enriched["json"],
        "rss_simple": simple["rss"],
        "atom_simple": simple["atom"],
        "json_simple": simple["json"],
    }


def feeds_exist(root: Path, *, relative_dir: str = DEFAULT_FEEDS_RELATIVE_DIR) -> bool:
    """True when all six feed artifacts exist under ``root / relative_dir``."""
    return all(p.is_file() for p in all_feed_paths(root, relative_dir=relative_dir).values())


def write_feeds(
    root: Path,
    *,
    rss: bytes,
    atom: bytes,
    json_feed: bytes,
    simple_rss: bytes,
    simple_atom: bytes,
    simple_json_feed: bytes,
    reporter: ProgressReporter | None = None,
    file_mode: int = 0o644,
    relative_dir: str = DEFAULT_FEEDS_RELATIVE_DIR,
) -> None:
    """Atomically publish all six feed artifacts under ``feeds/``."""
    progress = reporter or NULL_REPORTER
    feeds_dir = root / relative_dir
    feeds_dir.mkdir(parents=True, exist_ok=True)
    paths = all_feed_paths(root, relative_dir=relative_dir)

    artifacts: list[tuple[str, Path, bytes]] = [
        (ENRICHED_FEED_NAMES["rss"], paths["rss"], rss),
        (ENRICHED_FEED_NAMES["atom"], paths["atom"], atom),
        (ENRICHED_FEED_NAMES["json"], paths["json"], json_feed),
        (SIMPLE_FEED_NAMES["rss"], paths["rss_simple"], simple_rss),
        (SIMPLE_FEED_NAMES["atom"], paths["atom_simple"], simple_atom),
        (SIMPLE_FEED_NAMES["json"], paths["json_simple"], simple_json_feed),
    ]

    # Tear window: after the first replace, on-disk feeds may disagree briefly.
    for _name, final, blob in progress.track(artifacts, desc="Write feeds", unit="file"):
        atomic_write_bytes(final, blob, mode=file_mode)
        logger.debug("Wrote {} ({} bytes)", final, len(blob))
    logger.info("Wrote {} feed files → {}", len(artifacts), feeds_dir)


def verify_feed_artifacts(root: Path, *, min_items: int) -> None:
    """Deep-validate on-disk enriched and simple ``feeds/`` projections."""
    from paul_graham_essay_feeds.verify import raise_on_failure, verify_feed_bytes

    kinds: tuple[Literal["enriched", "simple"], ...] = ("enriched", "simple")
    for kind in kinds:
        kind_paths = feed_paths(root, kind=kind)
        try:
            rss = kind_paths["rss"].read_bytes()
            atom = kind_paths["atom"].read_bytes()
            json_feed = kind_paths["json"].read_bytes()
        except OSError as exc:
            raise FeedError(f"Missing {kind} feed artifacts under feeds/: {exc}") from exc
        raise_on_failure(
            verify_feed_bytes(
                rss=rss,
                atom=atom,
                json_feed=json_feed,
                min_items=min_items,
                kind=kind,
            )
        )


def _entry_summary(summary: str | None, title: str) -> str:
    """Short feed summary: source text when present, else title blurb."""
    text = summary if summary else blurb(title)
    out = truncate_text(text, FEED_SUMMARY_CHARS)
    if out:
        return out
    return truncate_text(blurb(title), FEED_SUMMARY_CHARS)


def _is_permalink(entry: FeedEntrySnapshot) -> bool:
    """True when the stable id is the essay URL (paulgraham.com permalinks)."""
    return entry.id == entry.url
