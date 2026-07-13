# Codex kickoff prompt

You are starting in the root of the new repository
`paul-graham-essay-feeds`. Implement the first complete, production-quality
version of this project. Do not stop after restating or refining the plan. Read
the supplied context, make the changes, run the full verification suite, and
leave the repository in a releasable state.

## Read before editing

Read these files in order:

1. `AGENTS.md`
2. `docs/index.md`
3. `docs/product-requirements.md`
4. `docs/architecture.md`
5. `docs/feed-formats.md`
6. `docs/implementation-plan.md`
7. `docs/acceptance-criteria.md`
8. `docs/security-and-operations.md`
9. `docs/deployment.md`
10. `reference/rss2-baseline/README.md`
11. `reference/rss2-baseline/update_feed.py`
12. `reference/rss2-baseline/test_update_feed.py`
13. `reference/rss2-baseline/validation.json`

Use the preserved RSS implementation as evidence for required behavior. Refactor
or port behavior into the new architecture rather than blindly wrapping or
copying a monolith.

## Goal

Create one safe updater and canonical state model that extracts the official
newest-to-oldest essay list from:

```text
https://paulgraham.com/articles.html
```

and atomically generates:

```text
feeds/rss.xml
feeds/atom.xml
feeds/feed.json
feeds/subscriptions.opml
```

The current audited baseline has 233 items. The implementation must not hard-code
233 as a permanent exact count; use it as the initial safety floor and preserve
strict change reconciliation.

## Mandatory architecture

Create a typed Python 3.13 package under:

```text
src/paul_graham_essay_feeds/
```

Use focused modules with responsibilities equivalent to:

```text
__init__.py
__main__.py
cli.py
config.py
domain.py
fetch.py
extract.py
reconcile.py
state.py
build.py
validation.py
io.py
renderers/
    __init__.py
    rss.py
    atom.py
    json_feed.py
    opml.py
```

Names may vary only when a nearby, demonstrably cleaner structure emerges.
Avoid circular imports and keep renderers pure: canonical models in, bytes out.

## Canonical data model

Define immutable, typed models for at least:

- `EssayItem`
  - `position: int`
  - `title: str`
  - `url: str`
  - `stable_id: str`
  - `first_seen_at: datetime`
  - `last_changed_at: datetime`
- extraction metadata
- source fetch metadata
- change set
- build metadata
- validation report

Persist canonical items to `data/essays.json` and updater state to
`data/state.json`. Use explicit schema versions and deterministic JSON
serialization.

### Identifier rules

- Internal Paul Graham essay pages use the canonical HTTPS URL as the stable ID.
- The two Turbify text resources use deterministic UUIDv5 URNs derived from their
  queryless canonical URLs, preserving the existing baseline identities where
  possible.
- The same stable ID must map into RSS `guid`, Atom `id`, and JSON Feed `id`.
- Stable IDs must never churn because a CDN cache-busting query changed.

### Time rules

The source index does not expose authoritative original publication dates.
Therefore:

- never invent RSS `pubDate`, Atom `published`, or JSON Feed `date_published`;
- persist `first_seen_at` and `last_changed_at` as **feed-observation metadata**;
- for the imported historical baseline, seed both values from the baseline
  import/build timestamp documented in the reference manifest;
- Atom requires `updated`, so use `last_changed_at` for each entry;
- change an existing item's `last_changed_at` only when its material canonical
  metadata changes;
- change feed-level build/updated timestamps only when logical feed content or
  feed configuration changes;
- a no-op update must preserve output bytes and modification times.

## Format requirements

Follow `docs/feed-formats.md` and the official specifications linked there.
At minimum:

### RSS 2.0

- `<rss version="2.0">` and exactly one channel.
- Required channel metadata and RFC-compatible `lastBuildDate`.
- Item title, canonical link, stable GUID, `dc:creator`, category, and a concise
  metadata-only description.
- `atom:link rel="self"` only when a real public feed URL is configured.
- No fabricated item dates.

### Atom 1.0

- Correct Atom namespace and media type assumptions.
- Exactly one feed `id`, `title`, and `updated`.
- Each entry has exactly one `id`, `title`, and `updated`, plus author, alternate
  link, and a concise summary.
- Add a self link only when configured.
- Use the official essay index as the alternate/home link.

### JSON Feed 1.1

- `version` must be `https://jsonfeed.org/version/1.1`.
- Each item has stable `id`, canonical `url`, title, and non-empty
  `content_text`; a link-only sentence is sufficient and must not copy essay
  content.
- Include top-level author metadata.
- Include `feed_url` only when configured.
- Omit fabricated publication and modification dates.

### OPML 2.0

This is a subscription catalog, not a fourth essay-item feed.

- Emit a valid OPML 2.0 document with required `head` and `body`.
- Catalog the generated RSS and Atom feeds as subscription outlines when public
  URLs are configured.
- Catalog JSON Feed as a link outline because OPML's classic subscription
  attributes are XML-feed-oriented.
- If no public base URL is configured, fail the OPML build explicitly rather
  than inserting placeholders or false URLs. The all-format `build` command may
  require a public base URL; local RSS/Atom/JSON render subcommands may remain
  available without one.

## Extraction and reconciliation

Preserve the baseline's protections:

- primary extraction via the source page's essay-row marker;
- controlled filtered-anchor fallback;
- exact title normalization and XML-safe text handling;
- canonical HTTPS URL resolution;
- host allowlist limited to `paulgraham.com` and the two protected Turbify
  resources;
- direct protection against legacy `paulgraham.com/https://...` URLs;
- conditional HTTP requests with ETag and Last-Modified state;
- bounded retry/backoff and a 5 MiB response cap;
- minimum item safety floor initialized to 233;
- automatic acceptance of newest-prefix additions;
- rejection by default of removals, retained-item reordering, and non-prefix
  historical additions;
- explicit CLI overrides for reviewed exceptions;
- atomic writes and backups;
- unchanged normalized content must not rewrite outputs.

## CLI

Expose a console script named:

```text
pg-essay-feeds
```

At minimum implement:

```text
pg-essay-feeds update
pg-essay-feeds build
pg-essay-feeds check
pg-essay-feeds diff
```

Expected behavior:

- `update`: fetch, extract, reconcile, build all configured formats, validate,
  and atomically write changed artifacts.
- `build`: build from persisted canonical data without fetching.
- `check`: validate all local state and outputs without network access.
- `diff`: fetch or read a supplied source file and report proposed changes
  without writing.

Support `--source-file`, `--dry-run`, `--allow-removals`,
`--allow-nonprefix-additions`, `--min-items`, and configuration of the public
base URL through CLI, environment, or `config.toml`.

Use standard-library `argparse` unless a runtime CLI dependency clearly lowers
complexity enough to justify itself. Zero runtime dependencies is the default.

## Configuration and deployment

> **Supersession (implemented):** primary hosting is **Vercel**, not GitHub Pages.
> Example public base: `https://paul-graham-essay-feeds.vercel.app/`.
> See `docs/deployment.md` and `config.example.toml`.

Use `config.example.toml` as the initial contract. Target production public base:

```text
https://paul-graham-essay-feeds.vercel.app/
```

Generate feed self URLs from the configured public base URL. Never embed a
placeholder URL.

Implement GitHub Actions workflows for:

1. CI on pushes and pull requests: install with `uv`, run format check, lint,
   type check, tests, deterministic fixture build, and local validation.
2. Scheduled/manual feed update: fetch, generate, validate, and open or update a
   narrowly scoped automation pull request when artifacts change.
3. Vercel deployment: assemble `site/` + `feeds/` into `public/` (see
   `vercel.json` / `scripts/assemble_public.sh`); prefer Git integration with
   Root Directory `paul-graham-essay-feeds`.

Use least-privilege workflow permissions. Pin third-party actions to full commit
SHAs or avoid them. Prefer official GitHub actions and `gh` where practical.

## Testing and validation

Implement the complete matrix in `docs/acceptance-criteria.md`. Required test
classes include:

- extraction and URL normalization;
- 233-item fixture parity and first/last boundary checks;
- reconciliation policy;
- stable IDs and timestamp persistence;
- RSS, Atom, JSON Feed, and OPML structural validation;
- exact cross-format item count/order/title/URL/ID parity;
- no-op byte and mtime stability;
- conditional HTTP 304 behavior using a local HTTP server;
- malformed URL and unexpected-host rejection;
- atomic-write failure behavior;
- CLI happy paths and failure exit codes;
- deterministic builds from the supplied fixture.

Do not use the public network in unit tests. Integration tests may be explicitly
marked and disabled by default.

## Repository cleanup and documentation

- Update `pyproject.toml` to a real package, version `0.1.0`, Python 3.13, with a
  console entry point.
- Generate a fresh `uv.lock`.
- Add `src/`, `tests/`, workflows, and the generated multi-format artifacts.
- Preserve `reference/rss2-baseline/` unchanged as an audit/reference snapshot.
- Replace planning language in `README.md` with end-user installation,
  subscription, update, validation, automation, and deployment instructions.
- Keep the unofficial-project disclaimer prominent.
- Add `CHANGELOG.md` release notes for `0.1.0`.
- Choose and add a code license only if the repository owner has already made
  that decision; otherwise record the unresolved license in the release
  checklist rather than guessing.

## Required verification before completion

Run and pass:

```bash
uv lock
uv sync --all-groups
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest
uv run pg-essay-feeds check
```

Also perform two explicit local scenarios:

1. Build all outputs from `fixtures/articles-2026-07-11.fragment.html` using a
   temporary public base URL and verify all four formats.
2. Run the same update again and prove that every generated output hash and
   modification time remains unchanged.

At the end, report:

- the architecture implemented;
- files added, removed, or migrated;
- exact commands run and their results;
- item count and first/last items in every content feed;
- any documented assumption or remaining blocker.

Do not claim completion if any required test or validator is skipped. If an
external validator cannot run before deployment, document it as a post-deploy
check while still completing all local structural validation.
