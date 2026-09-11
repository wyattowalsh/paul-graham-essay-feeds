"""Assemble the GitHub Pages artifact from committed ``feeds/``.

This is a deploy projection, not a second publisher. The durable product remains
root ``catalog.json`` plus six flat files under ``feeds/``. Root and ``/feeds/``
aliases are byte copies of those committed files. ``/latest/*`` is a bounded
newest-first projection with its own feed-level identity (PGF-FINAL-001).
``assets/brand/site/`` is copied into the artifact (PGF-FINAL-007).
"""

from __future__ import annotations

import argparse
import json
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Final, Literal
from urllib.parse import urljoin, urlsplit, urlunsplit

from paul_graham_essay_feeds.feeds import (
    ENRICHED_FEED_NAMES,
    SIMPLE_FEED_NAMES,
    feed_self_url,
)
from paul_graham_essay_feeds.models import (
    ATOM_NS,
    BRAND_IMAGE_ALT,
    DC_NS,
    FEED_ID_LATEST,
    FEED_ID_SIMPLE_LATEST,
    FEED_TITLE_LATEST,
    FEED_TITLE_SIMPLE_LATEST,
    HOST_PUBLIC_BASE_URL,
    LATEST_FEED_ITEMS,
    FeedError,
)

Kind = Literal["rss", "atom", "json"]
_FEED_NAMES: tuple[str, ...] = tuple(ENRICHED_FEED_NAMES.values()) + tuple(
    SIMPLE_FEED_NAMES.values()
)
_PAGES_FEED_COPIES: Final[frozenset[str]] = frozenset(_FEED_NAMES) | {
    f"feeds/{name}" for name in _FEED_NAMES
}
_PAGES_LATEST: Final[frozenset[str]] = frozenset(f"latest/{name}" for name in _FEED_NAMES)
_PAGES_BRAND_FILES: Final[frozenset[str]] = frozenset(
    {
        "app-icon-192x192.png",
        "app-icon-512x512.png",
        "app-icon-maskable-192x192.png",
        "app-icon-maskable-512x512.png",
        "apple-touch-icon.png",
        "atom-icon-144x144.png",
        "atom-logo-512x256.png",
        "favicon-48x48.png",
        "favicon-96x96.png",
        "favicon.ico",
        "favicon.svg",
        "feed-favicon-64x64.png",
        "feed-icon-512x512.png",
        "open-graph-1200x630.jpg",
        "rss-channel-image-144x144.png",
        "site.webmanifest",
    }
)
_PAGES_EXPECTED_FILES: Final[frozenset[str]] = (
    frozenset({".nojekyll", "index.html"}) | _PAGES_FEED_COPIES | _PAGES_LATEST | _PAGES_BRAND_FILES
)
_INDEX_HREFS: Final[tuple[str, ...]] = (
    "rss.xml",
    "rss.simple.xml",
    "atom.xml",
    "atom.simple.xml",
    "feed.json",
    "feed.simple.json",
)
_INDEX_FORMATS: Final[tuple[tuple[str, str, str], ...]] = (
    ("RSS 2.0", "rss.simple.xml", "rss.xml"),
    ("Atom 1.0", "atom.simple.xml", "atom.xml"),
    ("JSON Feed 1.1", "feed.simple.json", "feed.json"),
)
_INDEX_CSS: Final[str] = """
:root {
  color-scheme: light dark;
  --pg-feeds-night: #061827;
  --pg-feeds-night-border: #173A49;
  --pg-feeds-emitter: #39FF70;
  --pg-feeds-paper: #F4EFE4;
  --paper: light-dark(var(--pg-feeds-paper), var(--pg-feeds-night));
  --ink: light-dark(var(--pg-feeds-night), var(--pg-feeds-paper));
  --muted: light-dark(#5d564c, #b7c4c8);
  --rule: light-dark(#d9cfc0, var(--pg-feeds-night-border));
  --mark: light-dark(var(--pg-feeds-night), var(--pg-feeds-emitter));
  --cta-ink: light-dark(var(--pg-feeds-paper), var(--pg-feeds-night));
  --font-display: "Iowan Old Style", Palatino, "Palatino Linotype", "Book Antiqua", Georgia, serif;
  --font-body: Charter, "Bitstream Charter", "Sitka Text", "Iowan Old Style", Georgia, serif;
  --font-mono: "SFMono-Regular", ui-monospace, Menlo, Consolas, monospace;
}
* { box-sizing: border-box; }
html { background: var(--paper); }
body {
  margin: 0 auto;
  max-width: 44rem;
  padding-block: clamp(2.25rem, 10vh, 6.25rem) 3.5rem;
  padding-inline: 1.25rem;
  font-family: var(--font-body);
  font-size: 1.05rem;
  line-height: 1.55;
  background: var(--paper);
  color: var(--ink);
}
.skip {
  position: absolute;
  inset-inline-start: 0.75rem;
  inset-block-start: 0.75rem;
  z-index: 1;
  background: var(--paper);
  color: var(--ink);
  padding: 0.4rem 0.7rem;
  transform: translateY(-150%);
}
.skip:focus, .skip:focus-visible {
  transform: none;
}
.brand {
  display: flex;
  gap: 1rem;
  align-items: flex-start;
  margin: 0 0 0.85rem;
}
.brand img {
  display: block;
  width: 3.5rem;
  height: 3.5rem;
  border-radius: 0.7rem;
  flex-shrink: 0;
}
.kicker {
  margin: 0 0 0.7rem;
  font-size: 0.78rem;
  font-weight: 650;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--mark);
}
h1 {
  margin: 0 0 0.8rem;
  font-family: var(--font-display);
  font-size: clamp(2rem, 2.4vw + 1.35rem, 2.7rem);
  font-weight: 600;
  line-height: 1.08;
  letter-spacing: -0.03em;
}
h1::after {
  content: "";
  display: block;
  width: 2.5rem;
  height: 3px;
  margin-block-start: 0.85rem;
  background: var(--pg-feeds-emitter);
}
h2 {
  margin: 0 0 0.45rem;
  font-family: var(--font-display);
  font-size: 1.2rem;
  font-weight: 600;
}
h3 {
  margin: 0 0 0.45rem;
  font-family: var(--font-display);
  font-size: 0.98rem;
}
.lede, .hint {
  color: var(--muted);
  max-width: 36rem;
}
.lede { margin: 0 0 1.75rem; }
.hint { margin: 0 0 0.9rem; }
a {
  color: inherit;
  text-underline-offset: 0.18em;
  transition: color 160ms ease, background-color 160ms ease;
}
a:hover { color: var(--mark); }
a:focus-visible {
  outline: 2px solid var(--mark);
  outline-offset: 3px;
}
.cta {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-block-size: 2.75rem;
  padding-inline: 1.15rem;
  padding-block: 0.7rem;
  background: var(--mark);
  color: var(--cta-ink);
  font-weight: 650;
  text-decoration: none;
}
.cta:hover {
  color: var(--cta-ink);
  background: color-mix(in oklch, var(--mark) 86%, var(--ink));
}
.also {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem 1rem;
  margin: 0.9rem 0 0;
  color: var(--muted);
  font-size: 0.95rem;
}
.start, .catalog { margin-block-end: 2rem; }
.formats { container-type: inline-size; }
.grid {
  display: grid;
  gap: 0.85rem;
  grid-template-columns: 1fr;
}
@container (min-inline-size: 32rem) {
  .grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
@media (min-width: 40rem) {
  .grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
article {
  border: 1px solid var(--rule);
  padding: 0.85rem 0.95rem 0.7rem;
}
article ul {
  list-style: none;
  margin: 0;
  padding: 0;
}
article a {
  display: flex;
  align-items: center;
  min-block-size: 2.5rem;
}
code {
  font-family: var(--font-mono);
  font-size: 0.86em;
  overflow-wrap: break-word;
}
footer {
  color: var(--muted);
  font-size: 0.86rem;
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    transition-duration: 0.01ms !important;
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
  }
}
"""


def kind_for_name(name: str) -> Kind:
    """Map a committed feed filename to a slice format."""
    if name.endswith(".json"):
        return "json"
    if "atom" in name:
        return "atom"
    return "rss"


def is_simple_feed_name(name: str) -> bool:
    """True when ``name`` is a simple-variant artifact basename."""
    return ".simple." in name


def latest_title(*, simple: bool, limit: int = LATEST_FEED_ITEMS) -> str:
    """Feed title identifying latest, configured length, and variant."""
    if limit == LATEST_FEED_ITEMS:
        return FEED_TITLE_SIMPLE_LATEST if simple else FEED_TITLE_LATEST
    variant = "Simple" if simple else "Enriched"
    return f"Paul Graham Essays — Latest {limit}, {variant} (Unofficial)"


def latest_description(*, limit: int = LATEST_FEED_ITEMS) -> str:
    """Description stating that this resource is a bounded newest-first projection."""
    return (
        "Bounded newest-first projection of the unofficial Paul Graham essay feeds "
        f"(at most {limit} items). Titles, links, page metadata, and short source "
        "excerpts — never complete essay bodies. Ordered newest to oldest from the "
        "official index. Dates may be when this feed observed or detected a change, "
        "not the essay's original publication date."
    )


def latest_atom_id(*, simple: bool) -> str:
    """Stable Atom feed id for a latest projection. Does not change item ids."""
    return FEED_ID_SIMPLE_LATEST if simple else FEED_ID_LATEST


def latest_json_feed_url(*, simple: bool) -> str:
    """Absolute JSON Feed self URL for a latest projection."""
    name = SIMPLE_FEED_NAMES["json"] if simple else ENRICHED_FEED_NAMES["json"]
    return _join_host_artifact(f"latest/{name}")


def _join_host_artifact(relative: str) -> str:
    emitted = HOST_PUBLIC_BASE_URL.strip()
    parts = urlsplit(emitted)
    directory_path = (parts.path or "").rstrip("/") + "/"
    base = urlunsplit((parts.scheme, parts.netloc, directory_path, "", ""))
    return urljoin(base, relative)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml_bytes(root: ET.Element, *, default_namespace: str | None = None) -> str:
    ET.indent(root, space="  ")
    if default_namespace is not None:
        ET.register_namespace("", default_namespace)
    try:
        body = ET.tostring(root, encoding="unicode")
    finally:
        if default_namespace is not None:
            ET.register_namespace("atom", ATOM_NS)
            ET.register_namespace("dc", DC_NS)
    body = body.strip() + "\n"
    if body.startswith("<?xml"):
        return body
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body


def _set_direct_text(parent: ET.Element, local: str, value: str, *, ns: str | None = None) -> None:
    tag = f"{{{ns}}}{local}" if ns else local
    el = next((child for child in parent if _local_name(child.tag) == local), None)
    if el is None:
        el = ET.Element(tag)
        parent.insert(0, el)
    el.text = value


def _project_rss(text: str, *, simple: bool, limit: int) -> str:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise FeedError("RSS is not well-formed XML") from exc
    if _local_name(root.tag) != "rss":
        raise FeedError("RSS root must be <rss>")
    channels = [child for child in list(root) if _local_name(child.tag) == "channel"]
    if len(channels) != 1:
        raise FeedError("RSS must contain exactly one channel")
    channel = channels[0]
    items = [child for child in list(channel) if _local_name(child.tag) == "item"]
    for item in items:
        channel.remove(item)
    _set_direct_text(channel, "title", latest_title(simple=simple, limit=limit))
    _set_direct_text(channel, "description", latest_description(limit=limit))
    self_href = feed_self_url(latest_json_feed_url(simple=simple), kind="rss")
    self_links = [
        child
        for child in channel
        if _local_name(child.tag) == "link" and child.get("rel") == "self"
    ]
    if self_links:
        for link in self_links:
            link.set("href", self_href)
            if not link.get("type"):
                link.set("type", "application/rss+xml")
    else:
        ET.SubElement(
            channel,
            f"{{{ATOM_NS}}}link",
            {
                "rel": "self",
                "type": "application/rss+xml",
                "href": self_href,
            },
        )
    for item in items[:limit]:
        channel.append(item)
    ET.register_namespace("atom", ATOM_NS)
    ET.register_namespace("dc", DC_NS)
    return _xml_bytes(root)


def _project_atom(text: str, *, simple: bool, limit: int) -> str:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise FeedError("Atom is not well-formed XML") from exc
    if root.tag != f"{{{ATOM_NS}}}feed":
        raise FeedError("Atom root must be a namespaced <feed>")
    entries = [child for child in list(root) if _local_name(child.tag) == "entry"]
    for entry in entries:
        root.remove(entry)
    _set_direct_text(root, "title", latest_title(simple=simple, limit=limit), ns=ATOM_NS)
    _set_direct_text(root, "id", latest_atom_id(simple=simple), ns=ATOM_NS)
    _set_direct_text(root, "subtitle", latest_description(limit=limit), ns=ATOM_NS)
    self_href = feed_self_url(latest_json_feed_url(simple=simple), kind="atom")
    self_links = [
        child for child in root if child.tag == f"{{{ATOM_NS}}}link" and child.get("rel") == "self"
    ]
    if self_links:
        for link in self_links:
            link.set("href", self_href)
            if not link.get("type"):
                link.set("type", "application/atom+xml")
    else:
        ET.SubElement(
            root,
            f"{{{ATOM_NS}}}link",
            {
                "rel": "self",
                "type": "application/atom+xml",
                "href": self_href,
            },
        )
    for entry in entries[:limit]:
        root.append(entry)
    return _xml_bytes(root, default_namespace=ATOM_NS)


def _project_json(text: str, *, simple: bool, limit: int) -> str:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise FeedError("JSON Feed is not valid JSON") from exc
    if not isinstance(data, dict):
        raise FeedError("JSON Feed root must be an object")
    if "items" not in data:
        data["items"] = []
    items = data["items"]
    if not isinstance(items, list):
        raise FeedError("JSON Feed items must be a list")
    data["items"] = items[:limit]
    data["title"] = latest_title(simple=simple, limit=limit)
    data["description"] = latest_description(limit=limit)
    data["feed_url"] = latest_json_feed_url(simple=simple)
    meta = data.get("_pg_essay_feeds")
    if isinstance(meta, dict):
        meta["item_count"] = len(data["items"])
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def slice_latest(
    text: str,
    kind: Kind,
    *,
    limit: int = LATEST_FEED_ITEMS,
    simple: bool = False,
) -> str:
    """Keep the first ``limit`` items and rewrite latest feed-level identity.

    Always rewrites title, description/subtitle, and self URL even when the
    source already has ``limit`` or fewer items.
    """
    if kind == "json":
        return _project_json(text, simple=simple, limit=limit)
    if kind == "rss":
        return _project_rss(text, simple=simple, limit=limit)
    return _project_atom(text, simple=simple, limit=limit)


def _index_cards() -> str:
    """Format cards with Simple first. Landing does not list ``/latest/*``."""
    cards: list[str] = []
    for title, simple, enriched in _INDEX_FORMATS:
        cards.append(
            "<article>"
            f"<h3>{title}</h3>"
            "<ul>"
            f"<li><a href='{simple}' aria-label='{title} simple'>Simple</a></li>"
            f"<li><a href='{enriched}' aria-label='{title} enriched'>Enriched</a></li>"
            "</ul>"
            "</article>"
        )
    return "".join(cards)


def index_html(*, origin: str = HOST_PUBLIC_BASE_URL) -> str:
    """Subscribe landing. Links are relative so local ``_site/`` still works."""
    base = origin.rstrip("/")
    catalog = _index_cards()
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<meta name="theme-color" content="#061827">
<meta name="description" content="Unofficial metadata-only RSS, Atom, and JSON
feeds for Paul Graham essays.">
<title>Paul Graham essay feeds (unofficial)</title>
<link rel="icon" href="favicon.ico" sizes="16x16 32x32 48x48">
<link rel="icon" href="favicon.svg" type="image/svg+xml" sizes="any">
<link rel="icon" href="favicon-48x48.png" type="image/png" sizes="48x48">
<link rel="apple-touch-icon" href="apple-touch-icon.png" sizes="180x180">
<link rel="manifest" href="site.webmanifest">
<link rel="alternate" type="application/rss+xml" title="Simple RSS" href="rss.simple.xml">
<link rel="alternate" type="application/atom+xml" title="Simple Atom" href="atom.simple.xml">
<link rel="alternate" type="application/feed+json" title="Simple JSON Feed" href="feed.simple.json">
<meta property="og:type" content="website">
<meta property="og:title" content="Paul Graham Essay Feeds">
<meta property="og:description" content="Unofficial metadata-only RSS, Atom, and JSON
Feed for Paul Graham essays.">
<meta property="og:url" content="{base}/">
<meta property="og:image" content="{base}/open-graph-1200x630.jpg">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="{BRAND_IMAGE_ALT}">
<meta name="twitter:card" content="summary_large_image">
<style>
{_INDEX_CSS}
</style>
</head>
<body>
<a class="skip" href="#feeds">Skip to feeds</a>
<main>
<header>
<div class="brand">
<img src="favicon.svg" width="56" height="56" alt="">
<div>
<p class="kicker">Unofficial · metadata only</p>
<h1>Paul Graham essay feeds</h1>
</div>
</div>
<p class="lede">Titles, links, and short excerpts — never complete essays.
Hosted on GitHub Pages at <code>{base}</code>.</p>
</header>
<section class="start" aria-labelledby="start-heading">
<h2 id="start-heading">Start here</h2>
<p class="hint">Simple feeds are title and link only — the usual subscribe choice.</p>
<p><a class="cta" href="rss.simple.xml">Subscribe to Simple RSS</a></p>
<p class="also">Also
<a href="atom.simple.xml">Simple Atom</a>
<a href="feed.simple.json">Simple JSON Feed</a>
</p>
</section>
<section class="catalog" id="feeds" aria-labelledby="catalog-heading">
<h2 id="catalog-heading">Full catalog</h2>
<p class="hint">Every essay currently on the official index.
Enriched adds a short source excerpt.</p>
<div class="formats"><div class="grid">{catalog}</div></div>
</section>
</main>
<footer>Root and <code>/feeds/</code> files are byte-identical to committed
<code>feeds/</code>. Simple is title-only; enriched adds short source excerpts.
<code>/latest/*</code> is still generated as a newest-20 window for existing
subscribers — not a third catalog.</footer>
</body>
</html>
"""


def _copy_pages_brand_assets(root: Path, dest: Path) -> None:
    """Overlay the deployable brand subset from ``assets/brand/site/``."""
    site_assets = root / "assets" / "brand" / "site"
    if not site_assets.is_dir():
        raise FeedError(f"Missing Pages brand assets: {site_assets}")
    names: set[str] = set()
    for path in site_assets.iterdir():
        if path.is_dir():
            raise FeedError("Pages brand assets must be a flat file set")
        if path.is_file():
            names.add(path.name)
    extra = names - _PAGES_BRAND_FILES
    missing = _PAGES_BRAND_FILES - names
    if extra or missing:
        raise FeedError(
            f"Pages brand asset set mismatch: extra={sorted(extra)} missing={sorted(missing)}"
        )
    shutil.copytree(site_assets, dest, dirs_exist_ok=True)


def assemble_pages(repo_root: Path, dest: Path) -> Path:
    """Copy committed feeds, write ``/latest/*``, index, brand files, and ``.nojekyll``.

    Also mirrors the six files under ``feeds/`` so the previous GitHub Pages
    layout (``/feeds/rss.xml``) keeps working.
    """
    root = Path(repo_root).resolve()
    dest = Path(dest)
    if not dest.is_absolute():
        dest = (Path.cwd() / dest).resolve()
    feeds = root / "feeds"
    if dest in (root, feeds):
        raise FeedError("Pages output must not be the repository root or feeds/")
    if not feeds.is_dir():
        raise FeedError(f"Missing feeds directory: {feeds}")
    if dest.exists():
        if dest.is_dir():
            shutil.rmtree(dest)
        else:
            dest.unlink()
    dest.mkdir(parents=True)
    latest_dir = dest / "latest"
    latest_dir.mkdir()
    mirror = dest / "feeds"
    mirror.mkdir()
    for name in _FEED_NAMES:
        src = feeds / name
        if not src.is_file():
            raise FeedError(f"Missing feed artifact: {src}")
        payload = src.read_bytes()
        (dest / name).write_bytes(payload)
        (mirror / name).write_bytes(payload)
        sliced = slice_latest(
            payload.decode("utf-8"),
            kind_for_name(name),
            simple=is_simple_feed_name(name),
        )
        (latest_dir / name).write_text(sliced, encoding="utf-8", newline="\n")
    _copy_pages_brand_assets(root, dest)
    (dest / "index.html").write_text(index_html(), encoding="utf-8", newline="\n")
    (dest / ".nojekyll").write_bytes(b"")
    return dest


def _relative_files(dest: Path) -> set[str]:
    return {path.relative_to(dest).as_posix() for path in dest.rglob("*") if path.is_file()}


def _rss_item_record(item: ET.Element) -> tuple[str, str, str, str, str]:
    fields = {_local_name(child.tag): (child.text or "") for child in item}
    return (
        fields.get("guid", ""),
        fields.get("link", ""),
        fields.get("title", ""),
        fields.get("description", ""),
        fields.get("pubDate", ""),
    )


def _atom_entry_record(entry: ET.Element) -> tuple[str, str, str, str, str, str]:
    ns = ATOM_NS
    title = entry.findtext(f"{{{ns}}}title") or ""
    entry_id = entry.findtext(f"{{{ns}}}id") or ""
    summary = entry.findtext(f"{{{ns}}}summary") or ""
    updated = entry.findtext(f"{{{ns}}}updated") or ""
    published = entry.findtext(f"{{{ns}}}published") or ""
    href = ""
    for link in entry.findall(f"{{{ns}}}link"):
        if link.get("rel") in (None, "alternate"):
            href = link.get("href") or ""
            break
    return (entry_id, href, title, summary, updated, published)


def _json_item_record(item: object) -> tuple[object, ...]:
    if not isinstance(item, dict):
        raise FeedError("JSON Feed item must be an object")
    return (
        item.get("id"),
        item.get("url"),
        item.get("title"),
        item.get("summary"),
        item.get("content_text"),
        item.get("date_published"),
        item.get("date_modified"),
    )


def _rss_self_href(root: ET.Element) -> str | None:
    channels = [child for child in root if _local_name(child.tag) == "channel"]
    if len(channels) != 1:
        return None
    for child in channels[0]:
        if _local_name(child.tag) == "link" and child.get("rel") == "self":
            href = child.get("href")
            return href.strip() if href else None
    return None


def _rss_channel_text(root: ET.Element, local: str) -> str | None:
    channels = [child for child in root if _local_name(child.tag) == "channel"]
    if len(channels) != 1:
        return None
    for child in channels[0]:
        if _local_name(child.tag) == local:
            return child.text
    return None


def _require_unique(ids: list[str], *, label: str) -> None:
    nonempty = [item_id for item_id in ids if item_id]
    if len(nonempty) != len(set(nonempty)):
        raise FeedError(f"{label} contains duplicate item ids")


def _verify_latest_rss(full: bytes, latest: bytes, *, simple: bool) -> None:
    try:
        full_root = ET.fromstring(full)
        latest_root = ET.fromstring(latest)
    except ET.ParseError as exc:
        raise FeedError("latest RSS is not well-formed XML") from exc
    full_items = [
        child
        for channel in full_root
        if _local_name(channel.tag) == "channel"
        for child in channel
        if _local_name(child.tag) == "item"
    ]
    latest_items = [
        child
        for channel in latest_root
        if _local_name(channel.tag) == "channel"
        for child in channel
        if _local_name(child.tag) == "item"
    ]
    expected = min(LATEST_FEED_ITEMS, len(full_items))
    if len(latest_items) != expected:
        raise FeedError(f"latest RSS item count {len(latest_items)} != expected {expected}")
    got = [_rss_item_record(item) for item in latest_items]
    _require_unique([record[0] or record[1] for record in got], label="latest RSS")
    prefix = [_rss_item_record(item) for item in full_items[:expected]]
    if prefix != got:
        raise FeedError("latest RSS items are not an ordered prefix of the full feed")
    expected_self = feed_self_url(latest_json_feed_url(simple=simple), kind="rss")
    actual_self = _rss_self_href(latest_root)
    if actual_self != expected_self:
        raise FeedError(f"latest RSS self URL {actual_self!r} != {expected_self!r}")
    title = _rss_channel_text(latest_root, "title")
    if title != latest_title(simple=simple):
        raise FeedError(f"latest RSS title {title!r} is incorrect")
    description = _rss_channel_text(latest_root, "description")
    if description != latest_description():
        raise FeedError("latest RSS description must identify a bounded projection")


def _verify_latest_atom(full: bytes, latest: bytes, *, simple: bool) -> None:
    try:
        full_root = ET.fromstring(full)
        latest_root = ET.fromstring(latest)
    except ET.ParseError as exc:
        raise FeedError("latest Atom is not well-formed XML") from exc
    if latest_root.tag != f"{{{ATOM_NS}}}feed":
        raise FeedError("latest Atom root must be a namespaced <feed>")
    full_entries = [child for child in full_root if _local_name(child.tag) == "entry"]
    latest_entries = [child for child in latest_root if _local_name(child.tag) == "entry"]
    expected = min(LATEST_FEED_ITEMS, len(full_entries))
    if len(latest_entries) != expected:
        raise FeedError(f"latest Atom entry count {len(latest_entries)} != expected {expected}")
    got = [_atom_entry_record(entry) for entry in latest_entries]
    _require_unique([record[0] for record in got], label="latest Atom")
    prefix = [_atom_entry_record(entry) for entry in full_entries[:expected]]
    if prefix != got:
        raise FeedError("latest Atom entries are not an ordered prefix of the full feed")
    atom_id = latest_root.findtext(f"{{{ATOM_NS}}}id")
    expected_id = latest_atom_id(simple=simple)
    if atom_id != expected_id:
        raise FeedError(f"latest Atom id {atom_id!r} != {expected_id!r}")
    expected_self = feed_self_url(latest_json_feed_url(simple=simple), kind="atom")
    actual_self = None
    for link in latest_root.findall(f"{{{ATOM_NS}}}link"):
        if link.get("rel") == "self":
            actual_self = (link.get("href") or "").strip() or None
            break
    if actual_self != expected_self:
        raise FeedError(f"latest Atom self URL {actual_self!r} != {expected_self!r}")
    title = latest_root.findtext(f"{{{ATOM_NS}}}title")
    if title != latest_title(simple=simple):
        raise FeedError(f"latest Atom title {title!r} is incorrect")
    subtitle = latest_root.findtext(f"{{{ATOM_NS}}}subtitle")
    if subtitle != latest_description():
        raise FeedError("latest Atom subtitle must identify a bounded projection")


def _verify_latest_json(full: bytes, latest: bytes, *, simple: bool) -> None:
    try:
        full_data = json.loads(full.decode("utf-8"))
        latest_data = json.loads(latest.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise FeedError("latest JSON Feed is not valid JSON") from exc
    if not isinstance(latest_data, dict) or not isinstance(full_data, dict):
        raise FeedError("JSON Feed root must be an object")
    full_items = full_data.get("items")
    latest_items = latest_data.get("items")
    if not isinstance(full_items, list) or not isinstance(latest_items, list):
        raise FeedError("JSON Feed items must be a list")
    expected = min(LATEST_FEED_ITEMS, len(full_items))
    if len(latest_items) != expected:
        raise FeedError(f"latest JSON item count {len(latest_items)} != expected {expected}")
    got = [_json_item_record(item) for item in latest_items]
    ids = [str(record[0] or "") for record in got]
    _require_unique(ids, label="latest JSON Feed")
    prefix = [_json_item_record(item) for item in full_items[:expected]]
    if prefix != got:
        raise FeedError("latest JSON items are not an ordered prefix of the full feed")
    expected_url = latest_json_feed_url(simple=simple)
    if latest_data.get("feed_url") != expected_url:
        raise FeedError(f"latest JSON feed_url {latest_data.get('feed_url')!r} != {expected_url!r}")
    if latest_data.get("title") != latest_title(simple=simple):
        raise FeedError(f"latest JSON title {latest_data.get('title')!r} is incorrect")
    if latest_data.get("description") != latest_description():
        raise FeedError("latest JSON description must identify a bounded projection")
    meta = latest_data.get("_pg_essay_feeds")
    if isinstance(meta, dict) and meta.get("item_count") != expected:
        raise FeedError("latest JSON _pg_essay_feeds.item_count must match items")


def verify_pages_artifact(dest: Path, *, repo_root: Path | None = None) -> None:
    """Fail closed if ``dest`` is not a complete, identity-correct Pages tree.

    Verifies the exact directory that would be uploaded: full-feed copies,
    ``/feeds/`` aliases, all six latest projections, index links, ``.nojekyll``,
    and absence of stale files.
    """
    dest = Path(dest).resolve()
    if not dest.is_dir():
        raise FeedError(f"Pages artifact is not a directory: {dest}")
    found = _relative_files(dest)
    extra = found - _PAGES_EXPECTED_FILES
    missing = _PAGES_EXPECTED_FILES - found
    if extra or missing:
        raise FeedError(
            f"Pages artifact file set mismatch: extra={sorted(extra)} missing={sorted(missing)}"
        )
    if (dest / ".nojekyll").read_bytes() != b"":
        raise FeedError("Pages .nojekyll must be an empty file")
    for name in _FEED_NAMES:
        root_bytes = (dest / name).read_bytes()
        mirror_bytes = (dest / "feeds" / name).read_bytes()
        if root_bytes != mirror_bytes:
            raise FeedError(f"/feeds/{name} is not a byte copy of /{name}")
        if repo_root is not None:
            committed = (Path(repo_root) / "feeds" / name).read_bytes()
            if root_bytes != committed:
                raise FeedError(f"/{name} is not a byte copy of committed feeds/{name}")
        latest = (dest / "latest" / name).read_bytes()
        simple = is_simple_feed_name(name)
        kind = kind_for_name(name)
        if kind == "rss":
            _verify_latest_rss(root_bytes, latest, simple=simple)
        elif kind == "atom":
            _verify_latest_atom(root_bytes, latest, simple=simple)
        else:
            _verify_latest_json(root_bytes, latest, simple=simple)
    html = (dest / "index.html").read_text(encoding="utf-8")
    for href in _INDEX_HREFS:
        if f"href='{href}'" not in html and f'href="{href}"' not in html:
            raise FeedError(f"index.html is missing a link to {href}")
        if not (dest / href).is_file():
            raise FeedError(f"index.html links to missing file {href}")
    for needle in (
        'rel="icon"',
        'rel="apple-touch-icon"',
        'rel="manifest"',
        'property="og:image"',
    ):
        if needle not in html:
            raise FeedError(f"index.html is missing {needle}")
    if "href='latest/" in html or 'href="latest/' in html:
        raise FeedError("index.html must not present /latest/* as a subscribe choice")


def main(argv: list[str] | None = None) -> int:
    """CI / ``just pages`` entry: write and verify the Pages artifact."""
    parser = argparse.ArgumentParser(
        description="Assemble GitHub Pages artifact from committed feeds/"
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--out", type=Path, default=Path("_site"))
    args = parser.parse_args(argv)
    dest = assemble_pages(args.repo_root, args.out)
    verify_pages_artifact(dest, repo_root=args.repo_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
