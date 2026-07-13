# Paul Graham Essay Feeds

Unofficial, automatically generated **metadata-only** feeds for every item on
Paul Graham’s official essays index:

- Source: <https://paulgraham.com/articles.html>
- Current catalog size: **233** items (safety floor; grows with newest-prefix additions)
- Order: newest → oldest, matching the official index

**This is an unofficial project.** It is not affiliated with or endorsed by
Paul Graham. Essay titles and content remain attributable to Paul Graham.

## Subscribe

After the project is deployed on **Vercel** (Root Directory = repository root):

| Format | URL | Content-Type |
|---|---|---|
| RSS 2.0 | `https://paul-graham-essay-feeds.vercel.app/feeds/rss.xml` | `application/rss+xml` |
| Atom 1.0 | `https://paul-graham-essay-feeds.vercel.app/feeds/atom.xml` | `application/atom+xml` |
| JSON Feed 1.1 | `https://paul-graham-essay-feeds.vercel.app/feeds/feed.json` | `application/feed+json` |
| OPML catalog | `https://paul-graham-essay-feeds.vercel.app/feeds/subscriptions.opml` | `text/x-opml` |

Configure `deployment.public_base_url` (or `--public-base-url`) to your real
Vercel production URL if it differs.

Local paths after a build:

```text
feeds/rss.xml
feeds/atom.xml
feeds/feed.json
feeds/subscriptions.opml
```

## Install

Requires Python 3.13+ and [uv](https://docs.astral.sh/uv/).

### One-shot with `uvx` (no clone)

Generate feeds into the **current directory** from this GitHub repo:

```bash
mkdir pg-feeds && cd pg-feeds
uvx --from git+https://github.com/wyattowalsh/paul-graham-essay-feeds \
  pg-essay-feeds update
```

That writes:

```text
feeds/rss.xml  feeds/atom.xml  feeds/feed.json  feeds/subscriptions.opml
data/essays.json  data/state.json
reports/validation.json
SHA256SUMS
```

Defaults use the Vercel public base
(`https://paul-graham-essay-feeds.vercel.app/`). Override with
`--public-base-url` or `PG_ESSAY_FEEDS_PUBLIC_BASE_URL`.

Help / version without generating:

```bash
uvx --from git+https://github.com/wyattowalsh/paul-graham-essay-feeds \
  pg-essay-feeds --help
```

### Local checkout

```bash
uv sync --all-groups
uv run pg-essay-feeds --help
```

## Update / build / check

```bash
# Fetch the live index, reconcile, build, validate, publish
uv run pg-essay-feeds update \
  --public-base-url https://paul-graham-essay-feeds.vercel.app/

# Deterministic offline build from a saved HTML snapshot
uv run pg-essay-feeds update \
  --source-file fixtures/articles-2026-07-11.fragment.html \
  --public-base-url https://paul-graham-essay-feeds.vercel.app/ \
  --force

# Rebuild from data/essays.json without network
uv run pg-essay-feeds build \
  --public-base-url https://paul-graham-essay-feeds.vercel.app/

# Offline validation only
uv run pg-essay-feeds check \
  --public-base-url https://paul-graham-essay-feeds.vercel.app/

# Preview proposed changes without writing
uv run pg-essay-feeds diff --source-file fixtures/articles-2026-07-11.fragment.html
```

Configuration: copy `config.example.toml` to `config.toml` (optional). Environment:

- `PG_ESSAY_FEEDS_PUBLIC_BASE_URL`
- `PG_ESSAY_FEEDS_MIN_ITEMS`
- `PG_ESSAY_FEEDS_CONFIG`

### Safety policy

- Newest-prefix essay additions are accepted automatically.
- Removals, retained-item reordering, and mid-history insertions **fail closed**.
- Reviewed overrides: `--allow-removals`, `--allow-nonprefix-additions`, `--min-items`.
- Conditional HTTP (ETag / Last-Modified), retries, 5 MiB response cap.
- Atomic multi-artifact publish; no-op updates preserve feed bytes and mtimes.

### Timestamps

The source index does not expose authoritative publication dates. The catalog
stores `first_seen_at` / `last_changed_at` as **feed-observation metadata** only.
RSS and JSON Feed omit item publication dates. Atom’s required `updated` field
uses observation times and must not be treated as original publish dates.

### HTTP fetch stack

Fetching uses the **Python standard library** (`urllib.request`) only — no
`httpx`, `requests`, or `trafilatura`. Trafilatura extracts article bodies; this
project needs the full HTML index structure (essay-row markers).

## Development quality gates

```bash
uv lock
uv sync --all-groups
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest
uv run pg-essay-feeds check --public-base-url https://paul-graham-essay-feeds.vercel.app/
```

## Deploy (Vercel)

```bash
bash scripts/assemble_public.sh   # builds public/ from site/ + feeds/
```

- `vercel.json` sets `buildCommand`, `outputDirectory=public`, and feed Content-Types.
- GitHub Actions live at `.github/workflows/` (repo root = package root).
- Optional workflow: `.github/workflows/deploy-vercel.yml` (needs
  `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID`).

## Google Colab

Regenerate feeds in the browser without a local Python 3.13 install:

1. Open [`notebooks/regenerate_feeds.ipynb`](notebooks/regenerate_feeds.ipynb) in
   [Google Colab](https://colab.research.google.com/)
   (File → Upload notebook, or open from GitHub once published).
2. Set `SOURCE_MODE` to `"live"` or `"fixture"`, and set `PUBLIC_BASE_URL`.
3. **Runtime → Run all**.

The notebook installs `uv` + Python 3.13, clones this repo, runs
`pg-essay-feeds update`, validates with `check`, and offers a zip download of
`feeds/`, `data/`, `reports/validation.json`, and `SHA256SUMS`.

## Automation

GitHub Actions workflows (repository root):

1. **CI** — format, lint, typecheck, tests, fixture build, check, assemble public/  
2. **Update feeds** — scheduled/manual update + check → automation PR  
3. **Deploy Vercel** — optional CLI deploy of `public/`

## License

MIT (see `LICENSE` at the git repository root, or project `NOTICE.md` for attribution notes).

## References

- RSS 2.0: <https://www.rssboard.org/rss-specification>
- Atom 1.0: <https://www.rfc-editor.org/rfc/rfc4287>
- JSON Feed 1.1: <https://www.jsonfeed.org/version/1.1/>
- OPML 2.0: <https://opml.org/spec2.opml>
- Audited RSS baseline (immutable): `reference/rss2-baseline/`
