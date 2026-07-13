# Repository instructions

## Mission

Generate and maintain unofficial metadata-only RSS 2.0, Atom 1.0, JSON Feed 1.1,
and OPML 2.0 feeds for https://paulgraham.com/articles.html.

## Engineering defaults

- Python 3.13 + `uv`; `src/` layout; zero runtime dependencies.
- Pure renderers; one canonical item sequence and stable IDs across formats.
- No network in unit tests (fixtures + local HTTP servers).
- Atomic publish, conditional HTTP, host allowlists, fail-closed reconciliation,
  no-op byte/mtime stability.

## Content rules

- No full essay bodies; no fabricated publication dates.
- Observation timestamps only (`first_seen_at` / `last_changed_at`).
- Newest-prefix additions OK; removals / reorders / mid-history inserts need flags.

## Outputs (generated under cwd / repo root)

- `feeds/rss.xml`, `feeds/atom.xml`, `feeds/feed.json`, `feeds/subscriptions.opml`
- `data/essays.json`, `data/state.json` (gitignored)
- `reports/validation.json`, `SHA256SUMS` (gitignored)

Committed publishable artifacts: **`feeds/`** only (Vercel static deploy).

## Quality gates

```bash
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest
uv run pg-essay-feeds check
```
