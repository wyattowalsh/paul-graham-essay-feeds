# ADR-001: Feed contract

**Status:** Accepted  
**Date:** 2026-07-25  
**Fixes:** F-004, F-005, F-015, F-033, F-034, F-035

## Decision

Emit metadata-only **RSS 2.0**, **Atom 1.0**, and **JSON Feed 1.1** from one immutable `FeedSnapshot`.

### Item fields

| Field | Source | RSS | Atom | JSON Feed |
| :--- | :--- | :--- | :--- | :--- |
| Stable id | catalog identity | `guid` (isPermaLink per policy) | `id` | `id` |
| URL | normalized allowlisted URL | `link` | `link rel=alternate` | `url` |
| Title | index/page title | `title` | `title` | `title` |
| Summary | short source-derived text | `description` | `summary` | `summary` + `content_text` (same text) |
| Published | exact `published_at` only | `pubDate` if present | `published` if present | `date_published` if present |
| Updated | `observed_updated_at` | — | `updated` (required, truthful) | `date_modified` if present |

### Feed-level

- RSS `lastBuildDate` and Atom feed `updated` = generation `logical_updated_at` (latest material change), **not** wall-clock.
- JSON Feed `feed_url` and Atom/RSS self links only when a valid public base URL is configured.
- No operational cache state in public feeds (no `_pg_essay_feeds` after migration).
- Cross-format **exact ordered parity** for id, url, title, summary.
- No invented publication dates; no 1970 semantic sentinel for Atom entry `updated`.
- No full essay bodies.

### Verification

Deep verifier enforces structure, parity, uniqueness, URL policy, timestamp awareness, Unicode integrity (reject unexpected U+FFFD), and summary length/quality rules.

## Consequences

Renderers accept only `FeedSnapshot`. Legacy wall-clock and 1970 sentinel paths are removed in Wave 2.
