#!/usr/bin/env bash
# Assemble site/ + feeds/ into public/ for Vercel static hosting.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
rm -rf public
mkdir -p public/feeds
cp site/index.html public/index.html
# Copy feed artifacts only (skip backups).
for f in rss.xml atom.xml feed.json subscriptions.opml; do
  if [[ -f "feeds/$f" ]]; then
    cp "feeds/$f" "public/feeds/$f"
  else
    echo "warning: missing feeds/$f" >&2
  fi
done
echo "Assembled public/ for Vercel ($(find public -type f | wc -l | tr -d ' ') files)"
