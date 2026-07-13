#!/usr/bin/env python3
"""Offline integrity checks for the planning and kickoff bundle."""

from __future__ import annotations

import hashlib
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED = {
    "AGENTS.md",
    "CODEX_KICKOFF_PROMPT.md",
    "README.md",
    "config.example.toml",
    "docs/index.md",
    "docs/product-requirements.md",
    "docs/architecture.md",
    "docs/feed-formats.md",
    "docs/implementation-plan.md",
    "docs/acceptance-criteria.md",
    "reference/rss2-baseline/update_feed.py",
    "reference/rss2-baseline/test_update_feed.py",
    "data/baseline-items.json",
    "feeds/rss.xml",
    "planning-manifest.json",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fail(message: str) -> None:
    raise RuntimeError(message)


def main() -> int:
    missing = sorted(path for path in REQUIRED if not (ROOT / path).is_file())
    if missing:
        fail(f"Missing required files: {missing}")

    data = json.loads((ROOT / "data/baseline-items.json").read_text(encoding="utf-8"))
    items = data["items"]
    if len(items) != 233 or data.get("item_count") != 233:
        fail("Baseline item count is not 233.")

    urls = [item["url"] for item in items]
    ids = [item["stable_id"] for item in items]
    titles = [item["title"] for item in items]
    if len(set(urls)) != 233 or len(set(ids)) != 233:
        fail("Baseline URLs or stable IDs are not unique.")
    if titles[0] != "How to Earn a Billion Dollars":
        fail("Unexpected first baseline title.")
    if titles[-1] != "This Year We Can End the Death Penalty in California":
        fail("Unexpected last baseline title.")

    root = ET.fromstring((ROOT / "feeds/rss.xml").read_bytes())
    rss_items = root.findall("./channel/item")
    if len(rss_items) != 233:
        fail("Seed RSS item count is not 233.")
    rss_pairs = [
        ((item.findtext("title") or "").strip(), (item.findtext("link") or "").strip())
        for item in rss_items
    ]
    expected_pairs = [(item["title"], item["url"]) for item in items]
    if rss_pairs != expected_pairs:
        fail("Seed RSS does not align with baseline canonical items.")

    manifest = json.loads((ROOT / "planning-manifest.json").read_text(encoding="utf-8"))
    for record in manifest["files"]:
        path = ROOT / record["path"]
        if not path.is_file():
            fail(f"Manifest file missing: {record['path']}")
        if path.stat().st_size != record["size_bytes"]:
            fail(f"Size mismatch: {record['path']}")
        if sha256(path) != record["sha256"]:
            fail(f"SHA-256 mismatch: {record['path']}")

    summary = {
        "valid": True,
        "repo_name": manifest["repo_name"],
        "baseline_item_count": len(items),
        "unique_urls": len(set(urls)),
        "unique_ids": len(set(ids)),
        "first_item": items[0],
        "last_item": items[-1],
        "manifest_file_count": len(manifest["files"]),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
