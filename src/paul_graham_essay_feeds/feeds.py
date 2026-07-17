"""Render and write RSS/Atom/JSON feed artifacts.

Note: this module is named ``feeds`` (package code); generated files live in
the repository ``feeds/`` directory.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from loguru import logger
from tqdm import tqdm

from paul_graham_essay_feeds.model import (
    ATOM_NS,
    AUTHOR,
    AUTHOR_URL,
    DC_NS,
    FEED_DESCRIPTION,
    FEED_ID,
    FEED_SUMMARY_CHARS,
    FEED_TITLE,
    GENERATOR,
    JSON_FEED_VERSION,
    SOURCE_URL,
    Essay,
    EssayCatalog,
    FeedError,
    rfc822,
    rfc3339,
    stable_updated,
    utc_now,
)

_CATALOG_NAME = "essays.json"
_MANIFEST_NAME = ".manifest.json"
_FEED_FILE_NAMES = ("rss.xml", "atom.xml", "feed.json")


def render_rss(essays: list[Essay], *, built_at: datetime) -> bytes:
    """RSS 2.0: title, link, guid, short description — no full essay body."""
    ET.register_namespace("dc", DC_NS)
    root = ET.Element("rss", {"version": "2.0"})
    ch = ET.SubElement(root, "channel")
    for tag, val in (
        ("title", FEED_TITLE),
        ("link", SOURCE_URL),
        ("description", FEED_DESCRIPTION),
        ("language", "en-US"),
        ("lastBuildDate", rfc822(built_at)),
        ("category", "Essays"),
        ("generator", GENERATOR),
        ("docs", "https://www.rssboard.org/rss-specification"),
        ("ttl", "1440"),
    ):
        ET.SubElement(ch, tag).text = val

    for e in essays:
        item = ET.SubElement(ch, "item")
        ET.SubElement(item, "title").text = e.title
        ET.SubElement(item, "link").text = e.url
        ET.SubElement(item, "description").text = e.feed_summary()
        ET.SubElement(item, f"{{{DC_NS}}}creator").text = AUTHOR
        ET.SubElement(item, "category").text = "Essays"
        ET.SubElement(
            item,
            "guid",
            {"isPermaLink": "true" if e.is_permalink else "false"},
        ).text = e.stable_id
        if e.published_at is not None:
            ET.SubElement(item, "pubDate").text = rfc822(e.published_at)

    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="utf-8", xml_declaration=False)
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + body + b"\n"


def render_atom(essays: list[Essay], *, built_at: datetime) -> bytes:
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
    ET.SubElement(feed, "updated").text = rfc3339(built_at)
    ET.SubElement(feed, "subtitle").text = FEED_DESCRIPTION
    author = ET.SubElement(feed, "author")
    ET.SubElement(author, "name").text = AUTHOR
    ET.SubElement(author, "uri").text = AUTHOR_URL
    ET.SubElement(
        feed,
        "link",
        {"rel": "alternate", "type": "text/html", "href": SOURCE_URL},
    )
    ET.SubElement(feed, "generator").text = GENERATOR

    for e in essays:
        entry = ET.SubElement(feed, "entry")
        ET.SubElement(entry, "title").text = e.title
        ET.SubElement(entry, "id").text = e.stable_id
        # Feed-level updated = built_at; entry updated never uses wall-clock for undated.
        updated = e.published_at or stable_updated(e.stable_id)
        ET.SubElement(entry, "updated").text = rfc3339(updated)
        if e.published_at is not None:
            ET.SubElement(entry, "published").text = rfc3339(e.published_at)
        ET.SubElement(
            entry,
            "link",
            {"rel": "alternate", "type": "text/html", "href": e.url},
        )
        ET.SubElement(entry, "summary", {"type": "text"}).text = e.feed_summary()

    ET.indent(feed, space="  ")
    body = ET.tostring(feed, encoding="utf-8", xml_declaration=False)
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + body + b"\n"


def render_json(essays: list[Essay], *, built_at: datetime | None = None) -> bytes:
    """JSON Feed 1.1: title, url, id, summary — no full essay body."""
    items: list[dict] = []
    for e in essays:
        short = e.feed_summary()
        item: dict = {
            "id": e.stable_id,
            "url": e.url,
            "title": e.title,
            "summary": short,
            # JSON Feed 1.1 SHOULD content_text — short metadata only, not full body.
            "content_text": short,
            "authors": [{"name": AUTHOR, "url": AUTHOR_URL}],
            "tags": ["Essays"],
            "language": "en",
        }
        if e.published_at is not None:
            item["date_published"] = rfc3339(e.published_at)
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
    if built_at is not None:
        payload["_pg_essay_feeds"] = {
            "generator": GENERATOR,
            "built_at": rfc3339(built_at),
            "item_count": len(items),
        }
    return (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def catalog_path(root: Path) -> Path:
    """Path to the persisted essay catalog JSON."""
    return root / "data" / _CATALOG_NAME


def load_catalog(root: Path) -> EssayCatalog | None:
    """Load ``data/essays.json`` when present and valid; else ``None``."""
    path = catalog_path(root)
    if not path.is_file():
        return None
    try:
        return EssayCatalog.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("Ignoring unreadable catalog {}: {}", path, exc)
        return None


def feeds_exist(root: Path) -> bool:
    """True when feed artifacts and ``feeds/.manifest.json`` exist under ``root``."""
    paths = feed_paths(root)
    return all(p.is_file() for p in paths.values()) and manifest_path(root).is_file()


def manifest_path(root: Path) -> Path:
    """Path to the committed feed integrity manifest."""
    return root / "feeds" / _MANIFEST_NAME


def write_feeds(
    root: Path,
    *,
    rss: bytes,
    atom: bytes,
    json_feed: bytes,
    essays: list[Essay],
    index_hash: str | None = None,
) -> None:
    """Stage then publish ``feeds/*``, write ``.manifest.json``, plus ``data/essays.json``.

    Catalog is gitignored best-effort and is not listed in the manifest.
    """
    feeds_dir = root / "feeds"
    feeds_dir.mkdir(parents=True, exist_ok=True)

    artifacts: list[tuple[str, Path, bytes]] = [
        ("rss.xml", feeds_dir / "rss.xml", rss),
        ("atom.xml", feeds_dir / "atom.xml", atom),
        ("feed.json", feeds_dir / "feed.json", json_feed),
    ]

    staged: list[tuple[Path, str, bytes]] = []  # (final, tmp, blob)
    try:
        for name, final, blob in tqdm(artifacts, desc="Stage feeds", unit="file"):
            fd, tmp = tempfile.mkstemp(dir=str(feeds_dir), prefix=f".{name}.")
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(blob)
                    handle.flush()
                    os.fsync(handle.fileno())
            except Exception:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
                raise
            staged.append((final, tmp, blob))

        # Tear window: after replaces begin and before .manifest.json is written,
        # on-disk feeds may disagree with each other or with a stale manifest.
        for final, tmp, blob in staged:
            os.replace(tmp, final)
            logger.debug("Wrote {} ({} bytes)", final, len(blob))
        staged.clear()

        file_meta = {
            name: {
                "bytes": len(blob),
                "sha256": hashlib.sha256(blob).hexdigest(),
            }
            for name, _final, blob in artifacts
        }
        manifest = {
            "version": 1,
            "item_count": len(essays),
            "files": file_meta,
        }
        manifest_bytes = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
        _atomic_write(manifest_path(root), manifest_bytes)
        logger.info(
            "Wrote {} ({} items, {} feed files)",
            manifest_path(root),
            len(essays),
            len(file_meta),
        )
    except Exception:
        for _final, tmp, _blob in staged:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
        raise

    catalog = EssayCatalog(
        updated_at=utc_now(),
        count=len(essays),
        index_hash=index_hash,
        items=essays,
    )
    payload = catalog.model_dump_json(indent=2) + "\n"
    _atomic_write(catalog_path(root), payload.encode("utf-8"))
    logger.info("Wrote catalog ({} items) → {}", len(essays), catalog_path(root))


def verify_feed_artifacts(root: Path, *, min_items: int) -> None:
    """Validate on-disk feeds against each other and ``feeds/.manifest.json``.

    Checks item-count parity across RSS/Atom/JSON (and ``≥ min_items``), that
    each JSON Feed item has ``content_text == summary`` within
    ``[1, FEED_SUMMARY_CHARS]``, and that the manifest hashes/bytes/item_count
    match the three committed feed files. Raises :class:`FeedError` on failure.
    """
    paths = feed_paths(root)
    for path in paths.values():
        if not path.is_file():
            raise FeedError(f"Missing feed artifact: {path}")

    try:
        rss_root = ET.parse(paths["rss"]).getroot()
    except ET.ParseError as exc:
        raise FeedError(f"rss.xml is not valid XML: {exc}") from exc
    rss_count = len(rss_root.findall(".//item"))

    try:
        atom_root = ET.parse(paths["atom"]).getroot()
    except ET.ParseError as exc:
        raise FeedError(f"atom.xml is not valid XML: {exc}") from exc
    atom_count = len(atom_root.findall(f".//{{{ATOM_NS}}}entry"))

    try:
        payload = json.loads(paths["json"].read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FeedError(f"feed.json is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise FeedError("feed.json root must be an object")
    items = payload.get("items")
    if not isinstance(items, list):
        raise FeedError("feed.json missing items array")
    json_count = len(items)

    if rss_count != atom_count or rss_count != json_count:
        raise FeedError(
            f"Feed count mismatch: RSS={rss_count} Atom={atom_count} JSON={json_count}"
        )
    if rss_count < min_items:
        raise FeedError(f"Feed item count {rss_count} below floor {min_items}")

    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise FeedError(f"feed.json items[{i}] must be an object")
        content_text = item.get("content_text")
        summary = item.get("summary")
        if not isinstance(content_text, str) or not isinstance(summary, str):
            raise FeedError(
                f"feed.json items[{i}] requires string content_text and summary"
            )
        if content_text != summary:
            raise FeedError(
                f"feed.json items[{i}]: content_text must equal summary"
            )
        n = len(content_text)
        if not (1 <= n <= FEED_SUMMARY_CHARS):
            raise FeedError(
                f"feed.json items[{i}]: content_text length {n} "
                f"not in [1, {FEED_SUMMARY_CHARS}]"
            )

    man_path = manifest_path(root)
    if not man_path.is_file():
        raise FeedError(f"Missing feed manifest: {man_path}")
    try:
        manifest = json.loads(man_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FeedError(f"feeds/.manifest.json is not valid JSON: {exc}") from exc
    if not isinstance(manifest, dict):
        raise FeedError("feeds/.manifest.json root must be an object")
    if manifest.get("version") != 1:
        raise FeedError(
            f"feeds/.manifest.json version must be 1, got {manifest.get('version')!r}"
        )
    item_count = manifest.get("item_count")
    if not isinstance(item_count, int) or item_count != rss_count:
        raise FeedError(
            f"Manifest item_count {item_count!r} does not match feed count {rss_count}"
        )
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise FeedError("feeds/.manifest.json missing files object")

    name_to_path = {
        "rss.xml": paths["rss"],
        "atom.xml": paths["atom"],
        "feed.json": paths["json"],
    }
    for name in _FEED_FILE_NAMES:
        meta = files.get(name)
        if not isinstance(meta, dict):
            raise FeedError(f"Manifest missing files.{name} object")
        path = name_to_path[name]
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise FeedError(f"Cannot read {path}: {exc}") from exc
        expected_bytes = meta.get("bytes")
        expected_sha = meta.get("sha256")
        if expected_bytes != len(data):
            raise FeedError(
                f"Manifest bytes mismatch for {name}: "
                f"manifest={expected_bytes!r} disk={len(data)}"
            )
        actual_sha = hashlib.sha256(data).hexdigest()
        if expected_sha != actual_sha:
            raise FeedError(
                f"Manifest sha256 mismatch for {name}: "
                f"manifest={expected_sha!r} disk={actual_sha!r}"
            )


def feed_paths(root: Path) -> dict[str, Path]:
    feeds_dir = root / "feeds"
    return {
        "rss": feeds_dir / "rss.xml",
        "atom": feeds_dir / "atom.xml",
        "json": feeds_dir / "feed.json",
    }
