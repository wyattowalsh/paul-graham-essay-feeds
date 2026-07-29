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

<br />

### Try without installing

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/wyattowalsh/paul-graham-essay-feeds/blob/main/notebook.ipynb)

**Open in Colab → Runtime → Run all → download `feeds.zip`.**

Beautiful public notebook: short HTML intro, one dial (enrich on/off), then
`uvx … update` + `check` → zip RSS / Atom / JSON (live link probes on;
report-only status panel). No local clone required.

</div>

---

## Paths

| Path | When |
| :--- | :--- |
| **[Open in Colab](https://colab.research.google.com/github/wyattowalsh/paul-graham-essay-feeds/blob/main/notebook.ipynb)** | Try now — Run all → `feeds.zip` |
| Local `uvx` below | Keep feeds on disk / automate |
| [DOCS.md](./DOCS.md) | Architecture, tests, CI |

---

## Local quick start

```bash
mkdir pg-feeds && cd pg-feeds
uvx --from git+https://github.com/wyattowalsh/paul-graham-essay-feeds \
  pg-essay-feeds update
```

Writes `feeds/` into the current directory. Point a feed reader at the local files.

> [!TIP]
> Default `update` enriches each essay (~1 HTTP GET per page) with **4** enrich
> workers (polite to paulgraham.com). Use `--no-enrich` (or
> `PG_ESSAY_FEEDS_ENRICH=false`) for a fast index-only run.

---

## What you get

| File | Format | Contents |
| :--- | :--- | :--- |
| `feeds/rss.xml` | RSS 2.0 | title, link, guid, short description |
| `feeds/atom.xml` | Atom 1.0 | same, Atom shape |
| `feeds/feed.json` | JSON Feed 1.1 | same + short `summary` / `content_text` |

> [!IMPORTANT]
> **Not included:** full essay bodies or OPML. Durable catalog SSOT is
> `catalog.json` (repo root). Public projections live in `feeds/` — the GitHub repo
> *is* the published product (no separate `site/` or publish command).

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

## Notebook (Colab / Jupyter)

[`notebook.ipynb`](./notebook.ipynb) — public-facing Colab for feed-reader users.
**Run all** → HTML intro + one form cell (`uvx` update/check → `feeds.zip`).

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/wyattowalsh/paul-graham-essay-feeds/blob/main/notebook.ipynb)

Only dial most people need: **Enrich** (default on; ~1 GET/essay for short
summaries). Live link checks stay on (report-only); probe issues show in an
amber status panel without blocking the zip. Output path is under Advanced
(`/content/pg-feeds`).

---

## CLI

```bash
# index only (no per-page summary scrape)
pg-essay-feeds update --no-enrich

# offline HTML file
pg-essay-feeds update --source-file articles.html --no-enrich

# force rewrite even when index hash is unchanged
pg-essay-feeds update --force

# verify feeds (parity + content_text)
pg-essay-feeds check

# skip live link probes (default on; failures are report-only)
pg-essay-feeds update --no-validate-links
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
| `PG_ESSAY_FEEDS_FORCE` | `false` | Bypass hash skip when index unchanged |
| `PG_ESSAY_FEEDS_QUIET` / `PG_ESSAY_FEEDS_VERBOSE` | `false` | Log levels |

See also: [DOCS.md → Configuration](./DOCS.md#configuration).

</details>

---

## Develop

Contributor docs (architecture, tests, CI): **[DOCS.md](./DOCS.md)**

```bash
uv sync --all-groups
just all    # lint + types + tests (≥90% cov) + check
```

| Doc | Audience |
| :--- | :--- |
| [README.md](./README.md) | Users |
| [DOCS.md](./DOCS.md) | Developers |
| [AGENTS.md](./AGENTS.md) | Coding agents |
| [notebook.ipynb](./notebook.ipynb) | Public Colab — Run all → `feeds.zip` |

---

## Notes

> [!WARNING]
> Unofficial — not affiliated with or endorsed by Paul Graham.

- No full essay bodies in feeds; short `description` / `summary` / JSON
  `content_text` only (same short `feed_summary()`, not the essay body).
  Copyright on essay text remains with the author.
- Month+year on a page is a hint only — it does **not** become `pubDate` /
  `published` / `date_published`.
- Stable ids from URLs; Turbify chapters use a UUID derived from the path.
- Unchanged index → skip enrich/write when `feed.json` `_pg_essay_feeds`
  `index_hash` + fingerprint match (unless `--force`).

---

## License

MIT — see [LICENSE](./LICENSE).
