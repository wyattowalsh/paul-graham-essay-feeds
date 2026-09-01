"""Assemble the GitHub Pages artifact from committed ``feeds/``.

This is a deploy projection, not a second publisher. The durable product remains
root ``catalog.json`` plus six flat files under ``feeds/``.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path
from typing import Literal

from paul_graham_essay_feeds.feeds import ENRICHED_FEED_NAMES, SIMPLE_FEED_NAMES
from paul_graham_essay_feeds.models import HOST_PUBLIC_BASE_URL, LATEST_FEED_ITEMS, FeedError

Kind = Literal["rss", "atom", "json"]
_FEED_NAMES: tuple[str, ...] = tuple(ENRICHED_FEED_NAMES.values()) + tuple(
    SIMPLE_FEED_NAMES.values()
)
_ITEM_RE = {
    "rss": re.compile(r"<item\b[\s\S]*?</item>", re.IGNORECASE),
    "atom": re.compile(r"<entry\b[\s\S]*?</entry>", re.IGNORECASE),
}


def kind_for_name(name: str) -> Kind:
    """Map a committed feed filename to a slice format."""
    if name.endswith(".json"):
        return "json"
    if "atom" in name:
        return "atom"
    return "rss"


def slice_latest(text: str, kind: Kind, *, limit: int = LATEST_FEED_ITEMS) -> str:
    """Keep the first ``limit`` items (feeds are newest-first)."""
    if kind == "json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise FeedError("JSON Feed is not valid JSON") from exc
        if not isinstance(data, dict):
            raise FeedError("JSON Feed root must be an object")
        items = data.get("items")
        if isinstance(items, list):
            data["items"] = items[:limit]
        title = data.get("title")
        if isinstance(title, str) and "Latest" not in title:
            data["title"] = title.replace("Enriched", "Latest enriched").replace(
                "Simple", "Latest simple"
            )
        return json.dumps(data, ensure_ascii=False, indent=2) + "\n"

    matches = _ITEM_RE[kind].findall(text)
    if len(matches) <= limit:
        return text
    keep = matches[:limit]
    first = text.index(keep[0])
    last_item = matches[-1]
    last = text.rindex(last_item) + len(last_item)
    return text[:first] + "\n".join(keep) + text[last:]


def index_html(*, origin: str = HOST_PUBLIC_BASE_URL) -> str:
    """Tiny subscribe page. Links are relative so local ``_site/`` still works."""
    base = origin.rstrip("/")
    rows = (
        ("RSS 2.0", "rss.xml", "rss.simple.xml", "latest/rss.xml"),
        ("Atom 1.0", "atom.xml", "atom.simple.xml", "latest/atom.xml"),
        ("JSON Feed 1.1", "feed.json", "feed.simple.json", "latest/feed.json"),
    )
    body = "".join(
        f"<tr><th scope='row'>{name}</th>"
        f"<td><a href='{full}'>full</a></td>"
        f"<td><a href='{simple}'>simple</a></td>"
        f"<td><a href='{latest}'>latest 20</a></td></tr>"
        for name, full, simple, latest in rows
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Paul Graham essay feeds (unofficial)</title>
<link rel="alternate" type="application/rss+xml" title="Simple RSS" href="rss.simple.xml">
<link rel="alternate" type="application/atom+xml" title="Simple Atom" href="atom.simple.xml">
<link rel="alternate" type="application/feed+json" title="Simple JSON Feed" href="feed.simple.json">
<style>
  :root {{ color-scheme: light dark; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0 auto; max-width: 40rem; padding: 18vh 1.25rem 3rem;
    font: 16px/1.5 ui-sans-serif, system-ui, sans-serif;
    background: light-dark(#f4efe4, #12100e);
    color: light-dark(#1a1714, #f3eee6);
  }}
  .kicker {{
    font-size: 0.72rem; letter-spacing: 0.16em; text-transform: uppercase;
    color: light-dark(#9b2f12, #ff6b3d); margin: 0 0 0.85rem;
  }}
  h1 {{
    font: 600 2.15rem/1.1 "Iowan Old Style", Palatino, "Palatino Linotype", Georgia, serif;
    letter-spacing: -0.03em; margin: 0 0 0.75rem;
  }}
  p {{ color: light-dark(#5c564e, #b9b1a6); margin: 0 0 1.5rem; max-width: 34rem; }}
  a {{ color: inherit; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th, td {{
    text-align: left; padding: 0.7rem 0;
    border-bottom: 1px solid light-dark(#e4dccf, #2a2622);
  }}
  th:first-child {{ font-weight: 650; }}
  code {{ font-size: 0.86em; }}
  footer {{ margin-top: 2rem; font-size: 0.82rem; color: light-dark(#8a8378, #8f877c); }}
</style>
</head>
<body>
<p class="kicker">Unofficial · metadata only</p>
<h1>Paul Graham essay feeds</h1>
<p>Titles, links, and short excerpts — never complete essays.
Hosted on GitHub Pages at <code>{base}</code>.</p>
<table>
  <thead><tr><th>Format</th><th>Enriched</th><th>Simple</th><th>Latest</th></tr></thead>
  <tbody>{body}</tbody>
</table>
<footer>Same bytes as the repository <code>feeds/</code> tree.
Simple is title-only; enriched adds short source excerpts.</footer>
</body>
</html>
"""


def assemble_pages(repo_root: Path, dest: Path) -> Path:
    """Copy committed feeds, write ``/latest/*``, index, and ``.nojekyll``.

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
        sliced = slice_latest(payload.decode("utf-8"), kind_for_name(name))
        (latest_dir / name).write_text(sliced, encoding="utf-8", newline="\n")
    (dest / "index.html").write_text(index_html(), encoding="utf-8", newline="\n")
    (dest / ".nojekyll").write_bytes(b"")
    return dest


def main(argv: list[str] | None = None) -> int:
    """CI / ``just pages`` entry: write the Pages artifact."""
    parser = argparse.ArgumentParser(
        description="Assemble GitHub Pages artifact from committed feeds/"
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--out", type=Path, default=Path("_site"))
    args = parser.parse_args(argv)
    assemble_pages(args.repo_root, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
