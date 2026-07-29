"""Render and write RSS/Atom/JSON feed artifacts from ``FeedSnapshot``.

Note: this module is named ``feeds`` (package code); generated files live in
the repository ``feeds/`` directory.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

from loguru import logger

from paul_graham_essay_feeds.catalog import atomic_write_bytes
from paul_graham_essay_feeds.models import (
    ATOM_NS,
    AUTHOR,
    AUTHOR_URL,
    DC_NS,
    FEED_DESCRIPTION,
    FEED_ID,
    FEED_SUMMARY_CHARS,
    FEED_TITLE,
    JSON_FEED_VERSION,
    NULL_REPORTER,
    SOURCE_URL,
    Catalog,
    FeedEntrySnapshot,
    FeedError,
    FeedSnapshot,
    Lifecycle,
    ProgressReporter,
    blurb,
    rfc822,
    rfc3339,
    truncate_text,
)


def catalog_to_feed_snapshot(
    catalog: Catalog,
    *,
    generator: str,
    public_base_url: str | None = None,
    index_hash: str | None = None,
    index_fingerprint: str | None = None,
) -> FeedSnapshot:
    """Project ACTIVE catalog entries into an immutable feed snapshot.

    Rules:

    - Only ``Lifecycle.ACTIVE`` entries, in ``catalog.entry_order``.
    - ``summary`` = catalog summary when present, else a title blurb.
    - ``observed_updated_at`` = entry value, else ``first_seen_at``; entries
      missing both are skipped (reconcile should set observation times).
    - ``logical_updated_at`` = max item ``observed_updated_at``, else
      ``catalog.index.last_checked_at``.
    - Never invents 1970-01-01 observation times.
    """
    items: list[FeedEntrySnapshot] = []
    for stable_id in catalog.entry_order:
        entry = catalog.entries.get(stable_id)
        if entry is None:
            raise FeedError(f"Catalog entry_order references missing entry: {stable_id!r}")
        if entry.lifecycle is not Lifecycle.ACTIVE:
            continue

        observed = entry.observed_updated_at or entry.first_seen_at
        if observed is None:
            # Reconcile is expected to set observed_updated_at; skip undated.
            continue

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
        # FeedSnapshot requires ≥1 item; index last_checked is the only
        # remaining candidate clock (never invent wall-clock or 1970).
        logical_updated_at = catalog.index.last_checked_at

    if not items or logical_updated_at is None:
        raise FeedError(
            "Catalog has no ACTIVE entries with observation timestamps for feed projection"
        )

    base = public_base_url.strip() if public_base_url else None
    if not base:
        base = None
    feed_url = f"{base.rstrip('/')}/feed.json" if base is not None else None

    return FeedSnapshot(
        logical_updated_at=logical_updated_at,
        generator=generator,
        feed_url=feed_url,
        public_base_url=base,
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
    ET.SubElement(feed, "id").text = FEED_ID
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

    for entry in snapshot.items:
        atom_entry = ET.SubElement(feed, "entry")
        ET.SubElement(atom_entry, "title").text = entry.title
        ET.SubElement(atom_entry, "id").text = entry.id
        # Required truthful observation clock — never 1970 sentinel.
        ET.SubElement(atom_entry, "updated").text = rfc3339(entry.observed_updated_at)
        if entry.published_at is not None:
            ET.SubElement(atom_entry, "published").text = rfc3339(entry.published_at)
        ET.SubElement(
            atom_entry,
            "link",
            {"rel": "alternate", "type": "text/html", "href": entry.url},
        )
        ET.SubElement(atom_entry, "summary", {"type": "text"}).text = entry.summary

    ET.indent(feed, space="  ")
    body = ET.tostring(feed, encoding="utf-8", xml_declaration=False)
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + body + b"\n"


def render_json(snapshot: FeedSnapshot) -> bytes:
    """JSON Feed 1.1: title, url, id, summary — no full essay body."""
    items: list[dict] = []
    for entry in snapshot.items:
        item: dict = {
            "id": entry.id,
            "url": entry.url,
            "title": entry.title,
            "summary": entry.summary,
            # JSON Feed 1.1 SHOULD content_text — short metadata only, not full body.
            "content_text": entry.summary,
            "authors": [{"name": AUTHOR, "url": AUTHOR_URL}],
            "tags": ["Essays"],
            "language": "en",
        }
        if entry.published_at is not None:
            item["date_published"] = rfc3339(entry.published_at)
        item["date_modified"] = rfc3339(entry.observed_updated_at)
        items.append(item)

    payload: dict = {
        "version": JSON_FEED_VERSION,
        "title": FEED_TITLE,
        "home_page_url": SOURCE_URL,
        "description": FEED_DESCRIPTION,
        "language": "en",
        "authors": [{"name": AUTHOR, "url": AUTHOR_URL}],
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


def feeds_exist(root: Path) -> bool:
    """True when all three feed artifacts exist under ``root``."""
    return all(p.is_file() for p in feed_paths(root).values())


def write_feeds(
    root: Path,
    *,
    rss: bytes,
    atom: bytes,
    json_feed: bytes,
    reporter: ProgressReporter | None = None,
    file_mode: int = 0o644,
) -> None:
    """Atomically publish ``feeds/rss.xml``, ``atom.xml``, and ``feed.json``."""
    progress = reporter or NULL_REPORTER
    feeds_dir = root / "feeds"
    feeds_dir.mkdir(parents=True, exist_ok=True)

    artifacts: list[tuple[str, Path, bytes]] = [
        ("rss.xml", feeds_dir / "rss.xml", rss),
        ("atom.xml", feeds_dir / "atom.xml", atom),
        ("feed.json", feeds_dir / "feed.json", json_feed),
    ]

    # Tear window: after the first replace, on-disk feeds may disagree briefly.
    for _name, final, blob in progress.track(artifacts, desc="Write feeds", unit="file"):
        atomic_write_bytes(final, blob, mode=file_mode)
        logger.debug("Wrote {} ({} bytes)", final, len(blob))
    logger.info("Wrote {} feed files → {}", len(artifacts), feeds_dir)


def verify_feed_artifacts(root: Path, *, min_items: int) -> None:
    """Deep-validate on-disk ``feeds/`` (structure + cross-format parity)."""
    from paul_graham_essay_feeds.verify import raise_on_failure, verify_feed_dir

    raise_on_failure(verify_feed_dir(root, min_items=min_items))


def feed_paths(root: Path) -> dict[str, Path]:
    feeds_dir = root / "feeds"
    return {
        "rss": feeds_dir / "rss.xml",
        "atom": feeds_dir / "atom.xml",
        "json": feeds_dir / "feed.json",
    }


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
