<div align="center">

# Paul Graham Essay Feeds

Unofficial **RSS 2.0**, **Atom 1.0**, and **JSON Feed 1.1** for
[paulgraham.com/articles.html](https://paulgraham.com/articles.html) —
correct `https` links, short descriptions, guids, and clean Turbify chapter URLs.

<!-- BADGES:START -->

[![CI](https://github.com/wyattowalsh/paul-graham-essay-feeds/actions/workflows/ci.yml/badge.svg?style=flat-square)](https://github.com/wyattowalsh/paul-graham-essay-feeds/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](./LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/downloads/)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)

<!-- BADGES:END -->

</div>

---

## Subscribe

No Python required. Point a feed reader at these hosted files.

**Simple (recommended)** — title and link only:

| Format | Subscribe |
| :--- | :--- |
| RSS 2.0 | [Subscribe](https://raw.githubusercontent.com/wyattowalsh/paul-graham-essay-feeds/main/feeds/rss.simple.xml) |
| Atom 1.0 | [Subscribe](https://raw.githubusercontent.com/wyattowalsh/paul-graham-essay-feeds/main/feeds/atom.simple.xml) |
| JSON Feed 1.1 | [Subscribe](https://raw.githubusercontent.com/wyattowalsh/paul-graham-essay-feeds/main/feeds/feed.simple.json) |

**Enriched** — short source-derived summaries (semantic gating rejects promo/chrome):

| Format | Subscribe |
| :--- | :--- |
| RSS 2.0 | [Subscribe](https://raw.githubusercontent.com/wyattowalsh/paul-graham-essay-feeds/main/feeds/rss.xml) |
| Atom 1.0 | [Subscribe](https://raw.githubusercontent.com/wyattowalsh/paul-graham-essay-feeds/main/feeds/atom.xml) |
| JSON Feed 1.1 | [Subscribe](https://raw.githubusercontent.com/wyattowalsh/paul-graham-essay-feeds/main/feeds/feed.json) |

> [!NOTE]
> **Accepted risk (PGF-2026-015):** GitHub raw serves these files as
> `text/plain` (the body is still RSS / Atom / JSON Feed). Strict readers
> that require `application/rss+xml` (or similar) should point at local
> `feeds/` from the CLI below. This project does not add GitHub Pages or
> `site/`.

---

## What you get

| Path | Format | Contents |
| :--- | :--- | :--- |
| `feeds/rss.simple.xml` | RSS 2.0 | **simple** — title/link blurbs only |
| `feeds/atom.simple.xml` | Atom 1.0 | simple |
| `feeds/feed.simple.json` | JSON Feed 1.1 | simple |
| `feeds/rss.xml` | RSS 2.0 | **enriched** — title, link, guid, short description |
| `feeds/atom.xml` | Atom 1.0 | enriched, Atom shape |
| `feeds/feed.json` | JSON Feed 1.1 | enriched + short `summary` / `content_text` |
| `catalog.json` | JSON | durable catalog SSOT (current index mirror) |

> [!IMPORTANT]
> **Not included:** full essay bodies or OPML. Durable catalog SSOT is
> `catalog.json` (repo root). Public projections live flat in `feeds/` — the GitHub
> repo *is* the published product (no separate `site/` or publish command).

---

## Why not a bare scrape?

| Concern | Typical scrapes | This project |
| :--- | :--- | :--- |
| Scheme | often `http://` | **`https://`** |
| Turbify chapters | `paulgraham.com/https://sep…` breakage | clean CDN URLs |
| Description | none | short summary (optional enrich) |
| Stable id | rare | **`guid` / JSON `id`** |
| Dates | invented day-1 | month+year → hint only; no feed date unless a real full day exists |
| Formats | RSS-ish only | **RSS + Atom + JSON** |

---

## Local CLI

```bash
mkdir pg-feeds && cd pg-feeds
uvx --from git+https://github.com/wyattowalsh/paul-graham-essay-feeds@main \
  pg-essay-feeds update
```

Writes `feeds/` into the current directory. Point a feed reader at the local files.

> Intended release is **1.0.0**; until the `v1.0.0` tag exists, install from `main`.

> [!TIP]
> Default `update` enriches due essays (~1 HTTP GET per due page, capped at 40
> per run; `--all-pages` for a full-corpus refresh) with **4** enrich
> workers (polite to paulgraham.com). Use `--no-enrich` (or
> `PG_ESSAY_FEEDS_ENRICH=false`) for a fast index-only run.

---

## CLI

```bash
# index only (no per-page summary scrape)
pg-essay-feeds update --no-enrich

# offline HTML file
pg-essay-feeds update --source-file articles.html --no-enrich

# seed in-memory catalog candidate from existing feeds; persist only after successful verification/publication
pg-essay-feeds update --from-feeds

# bypass refresh-planner no-op (rewrite even when nothing is due)
pg-essay-feeds update --force

# quiet success → zero bytes on stdout/stderr; machine sinks still get action=
pg-essay-feeds update -q --result-file /tmp/pg-action.txt
# ($GITHUB_OUTPUT also receives action=unchanged|state_changed|updated when set)

# verify feeds + required catalog.json (parity + content_text)
pg-essay-feeds check

# skip live link probes (default on; failures are report-only)
pg-essay-feeds update --no-validate-links

# explicit repair for irrecoverable .cache/materialize.json (quarantines pointer + generation)
pg-essay-feeds update --abandon-recovery
```

> [!NOTE]
> CLI flags override env Settings **only when explicitly passed**. Full
> precedence and flag tables: [DOCS.md → CLI reference](./DOCS.md#cli-reference).

<details>
<summary><strong>Extended CLI examples</strong></summary>

```bash
# custom output root
pg-essay-feeds update --repo-root /tmp/pg-feeds --no-enrich

# override extract/check floor (default: Settings.min_items / MIN_ITEMS)
pg-essay-feeds update --min-items 10

# quieter / noisier logs
pg-essay-feeds update -q
pg-essay-feeds update -v

# verify a specific tree
pg-essay-feeds check --repo-root /tmp/pg-feeds
```

</details>

---

## Configuration

Environment prefix: `PG_ESSAY_FEEDS_` ([pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)).

| Env var | Default |
| :--- | :--- |
| `PG_ESSAY_FEEDS_SOURCE_URL` | official `articles.html` |
| `PG_ESSAY_FEEDS_REPO_ROOT` | cwd |
| `PG_ESSAY_FEEDS_MIN_ITEMS` | safety floor (see Settings) |
| `PG_ESSAY_FEEDS_TIMEOUT` | `30` |
| `PG_ESSAY_FEEDS_ENRICH` | `true` |
| `PG_ESSAY_FEEDS_VALIDATE_LINKS` | `true` |
| `PG_ESSAY_FEEDS_LINK_WORKERS` | `4` |
| `PG_ESSAY_FEEDS_ENRICH_WORKERS` | `4` |
| `PG_ESSAY_FEEDS_STALE_AFTER_DAYS` | `30` |
| `PG_ESSAY_FEEDS_PUBLIC_BASE_URL` | unset |
| `PG_ESSAY_FEEDS_ALLOW_DISCOVERY_FALLBACK` | `true` |
| `PG_ESSAY_FEEDS_HOST_COOLDOWN_SECONDS` | `0.05` |

```bash
export PG_ESSAY_FEEDS_ENRICH=false   # optional: skip per-page scrapes
```

<details>
<summary><strong>Full env table</strong> (retries, workers, timeouts)</summary>

| Env var | Default | Notes |
| :--- | :--- | :--- |
| `PG_ESSAY_FEEDS_SOURCE_URL` | official articles.html | Index URL |
| `PG_ESSAY_FEEDS_REPO_ROOT` | cwd | Resolved absolute path |
| `PG_ESSAY_FEEDS_MIN_ITEMS` | safety floor | Fail if fewer index items |
| `PG_ESSAY_FEEDS_TIMEOUT` | `30` | Index fetch timeout |
| `PG_ESSAY_FEEDS_RETRIES` | `3` | Tenacity attempts = retries+1 |
| `PG_ESSAY_FEEDS_MAX_BYTES` | 5 MiB | Response size cap |
| `PG_ESSAY_FEEDS_VALIDATE_LINKS` | `true` | Live probes (report-only; set `false` to skip) |
| `PG_ESSAY_FEEDS_LINK_TIMEOUT` | `10` | Per-probe timeout |
| `PG_ESSAY_FEEDS_LINK_WORKERS` | `4` | Live-probe thread pool (not enrich) |
| `PG_ESSAY_FEEDS_ENRICH` | `true` | Per-page short summary scrape |
| `PG_ESSAY_FEEDS_ENRICH_WORKERS` | `4` | Enrich thread pool |
| `PG_ESSAY_FEEDS_ENRICH_TIMEOUT` | `15` | Per-page timeout |
| `PG_ESSAY_FEEDS_FORCE` | `false` | Bypass refresh-planner no-op |
| `PG_ESSAY_FEEDS_PUBLIC_BASE_URL` | unset | Public base for feed self links |
| `PG_ESSAY_FEEDS_STALE_AFTER_DAYS` | `30` | Re-fetch page metadata after N days |
| `PG_ESSAY_FEEDS_ALLOW_DISCOVERY_FALLBACK` | `true` | Sparse-marker discovery fallback |
| `PG_ESSAY_FEEDS_HOST_COOLDOWN_SECONDS` | `0.05` | Min seconds between requests to the same host |
| `PG_ESSAY_FEEDS_QUIET` / `PG_ESSAY_FEEDS_VERBOSE` | `false` | Log levels |

See also: [DOCS.md → Configuration](./DOCS.md#configuration).

</details>

---

## Maintainer / custom generation

Generate a private `feeds/` tree (or a zip) instead of subscribing to the
hosted files.

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/wyattowalsh/paul-graham-essay-feeds/blob/main/notebook.ipynb)

[`notebook.ipynb`](./notebook.ipynb) — **Run all** → HTML intro + one form
cell (`uvx` update/check → `feeds.zip`). Dials: **Enrich** (default on;
~1 GET/essay for short summaries), **Auto-download** (browser download
after zip). Zips all six `feeds/{rss,atom,feed}{,.simple}.*` files.
Reachability issues show in a report-only status panel without blocking
the zip. Output path is under Advanced (`/content/pg-feeds`).

Intended release is **1.0.0**; until the `v1.0.0` tag exists, install from `main`.

Local clone:

```bash
uv sync --all-groups
uv run pg-essay-feeds update
```

---

## Develop

Contributor docs (architecture, tests, CI): **[DOCS.md](./DOCS.md)**

```bash
uv sync --all-groups
just all    # lint + types + tests (≥90% cov) + check
```

| Doc | Audience |
| :--- | :--- |
| [README.md](./README.md) | Users — hosted subscribe + local CLI |
| [DOCS.md](./DOCS.md) | Developers |
| [CONTRIBUTING.md](./CONTRIBUTING.md) | Contributors |
| [SECURITY.md](./SECURITY.md) | Vulnerability reports |
| [NOTICE](./NOTICE) | Software MIT; essay text remains Paul Graham's |
| [AGENTS.md](./AGENTS.md) | Coding agents |
| [notebook.ipynb](./notebook.ipynb) | Maintainer / custom generation — Run all → `feeds.zip` |

---

## Notes

> [!WARNING]
> Unofficial — not affiliated with or endorsed by Paul Graham.

- No full essay bodies in feeds; short `description` / `summary` / JSON
  `content_text` only (same short `feed_summary()`, not the essay body).
  Copyright on essay text remains with the author. See [NOTICE](./NOTICE).
- Month+year on a page is a hint only — it does **not** become `pubDate` /
  `published` / `date_published`.
- Stable ids from URLs; Turbify chapters use a UUID derived from the path.
- Catalog refresh planner (F-001): skip enrich/write when the planner says
  nothing is due (unless `--force`). Index hash alone is not a skip reason.

---

## License

Software is **MIT** — see [LICENSE](./LICENSE).

Essay titles, URLs, and short source-derived summaries remain Paul Graham's
(or the original rights holder's). The MIT license does **not** relicense
third-party essay text. See [NOTICE](./NOTICE).
