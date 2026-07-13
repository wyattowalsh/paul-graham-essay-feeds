# Repository instructions

## Mission

Build and maintain complete, metadata-only RSS 2.0, Atom 1.0, JSON Feed 1.1,
and OPML 2.0 outputs for the official Paul Graham essays index. Treat
`reference/rss2-baseline/` as an audited behavioral baseline, not as the target
architecture.

## Required context

Before substantial work, read `docs/index.md` and the documents it links for the
relevant area. The first implementation task is fully specified in
`CODEX_KICKOFF_PROMPT.md`.

## Engineering defaults

- Target Python 3.13 and manage the project with `uv`.
- Use a `src/` layout, explicit typing, dataclasses or similarly small typed
  models, NumPy-style docstrings, and clean module boundaries.
- Keep runtime dependencies at zero unless a dependency has a documented,
  compelling benefit. Dev-only Ruff, ty, and pytest are expected.
- Prefer deterministic pure renderers over format-specific mutable logic.
- Use one canonical item sequence and one stable identity across every format.
- Keep network access out of unit tests. Use fixtures and local HTTP servers.
- Preserve atomic writes, conditional requests, bounded retries, response-size
  limits, URL allowlisting, no-op byte stability, backups, and explicit change
  reconciliation from the RSS baseline.

## Data and content rules

- Do not copy full essay bodies.
- Do not fabricate original publication dates.
- Persist feed-observation timestamps separately and document their semantics.
- Canonicalize ordinary essay URLs to `https://paulgraham.com/...`.
- Preserve the two Turbify chapter links directly and prevent double-prefixing.
- New items may be accepted as a newest-first prefix. Removals, retained-item
  reordering, and mid-history insertions require explicit override and review.

## Output contract

Generate and validate exactly:

- `feeds/rss.xml`
- `feeds/atom.xml`
- `feeds/feed.json`
- `feeds/subscriptions.opml`
- `data/essays.json`
- `data/state.json`
- `reports/validation.json`
- `SHA256SUMS`

All content feeds must have identical item count, order, titles, canonical URLs,
and stable IDs.

## Quality gates

Before declaring work complete, run:

```bash
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest
uv run pg-essay-feeds check
```

Update documentation when behavior, configuration, commands, output paths, or
security assumptions change. Do not commit or rewrite Git history unless the
user explicitly asks.
