# Paul Graham Essays RSS

A complete, standards-conformant RSS 2.0 feed generated from Paul Graham’s
official essays index:

- Source: `https://paulgraham.com/articles.html`
- Feed: `paul-graham-essays.rss.xml`
- Current items: **233**
- Order: newest to oldest, exactly matching the source’s main essay list

## Update it

### macOS / Linux

```bash
./update.sh
```

### Windows PowerShell

```powershell
.\update.ps1
```

That is the entire normal update workflow. The updater uses only the Python
standard library. It finds Python 3.11+ automatically; if no suitable Python is
installed but `uv` is available, the launcher uses `uv` with Python 3.13. You
can also run `uv run --locked update_feed.py` directly.

Validate the existing local feed without making a network request:

```bash
./update.sh --check
```

Preview a prospective update without writing files:

```bash
./update.sh --dry-run
```

## Set the deployed feed URL

After hosting the XML file, provide its public URL so the updater emits the
recommended Atom self-link:

```bash
RSS_SELF_URL=https://example.com/feeds/paul-graham-essays.xml ./update.sh
```

PowerShell:

```powershell
$env:RSS_SELF_URL = "https://example.com/feeds/paul-graham-essays.xml"
.\update.ps1
```

The packaged feed intentionally omits `atom:link rel="self"` because no real
deployment URL was supplied. It is better to omit that optional extension than
to publish a false or placeholder URL.

## What the updater guarantees

- Uses the source page’s essay-row marker as the primary extractor.
- Falls back to a filtered-anchor extractor if Paul Graham redesigns the page.
- Canonicalizes Paul Graham links to absolute `https://paulgraham.com/...` URLs.
- Preserves the two external ANSI Common Lisp chapter links without the legacy
  malformed `paulgraham.com/https://...` prefix.
- Removes empty trailing query parameters from those CDN links.
- Produces stable GUIDs, including query-independent GUIDs for the CDN items.
- Refuses unexpected removals or reordering by default.
- Allows newly published essays as a prefix without changing a fixed item count.
- Uses conditional HTTP requests when the server exposes `ETag` or
  `Last-Modified` metadata.
- Retries transient HTTP failures with bounded backoff.
- Enforces a response-size limit and an item-count safety floor.
- Writes files atomically and keeps one `.bak` copy before changed rewrites.
- Does not rewrite the feed or churn `lastBuildDate` when nothing changed.
- Emits deterministic manifests, validation reports, and SHA-256 checksums.

A legitimate source-page deletion can be accepted explicitly while setting a
new safety floor. For example, after one intentional deletion:

```bash
./update.sh --allow-removals --min-items 232
```

A legitimate historical insertion outside the newest-item prefix can be
accepted explicitly:

```bash
./update.sh --allow-nonprefix-additions
```

These flags are intentionally opt-in so parser regressions or partial source
responses cannot silently destroy the feed.

## RSS design decisions

The feed follows RSS 2.0 and the RSS Advisory Board best-practices profile:

- One `<rss version="2.0">` root and one `<channel>`.
- Required channel `title`, `link`, and `description` elements.
- RFC 822-compatible UTC `lastBuildDate`.
- One unique, stable `guid` per item.
- Absolute HTTPS item links.
- `dc:creator` for Paul Graham, because the core RSS `author` element requires
  an email address.
- No fabricated item `pubDate` values. The essays index does not provide
  authoritative per-item timestamps, and RSS 2.0 makes `pubDate` optional.
- No full-text copying. Each item includes a concise description and links to
  the official source.

Recommended HTTP response header when hosting:

```text
Content-Type: application/rss+xml; charset=utf-8
```

## Files

- `paul-graham-essays.rss.xml`: finalized feed
- `update.sh`: one-command macOS/Linux launcher
- `update.ps1`: one-command Windows launcher
- `update_feed.py`: updater, reconciler, generator, and validator
- `paul-graham-essays.items.json`: normalized source manifest
- `validation.json`: machine-readable assurance report
- `test_update_feed.py`: standard-library unit tests
- `pyproject.toml` and `uv.lock`: zero-dependency `uv` project metadata
- `SHA256SUMS`: integrity hashes

Run the tests:

```bash
python3 -m unittest -v
```

## Simple automation

Daily cron example:

```cron
17 6 * * * cd /path/to/paul-graham-essays-rss && ./update.sh >> update.log 2>&1
```

The updater exits with status `0` for a valid updated or unchanged feed and
status `1` for fetch, extraction, reconciliation, or validation failures.
