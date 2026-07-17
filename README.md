# Paul Graham Essay Feeds

Unofficial **RSS 2.0**, **Atom 1.0**, and **JSON Feed 1.1** files for
[paulgraham.com/articles.html](https://paulgraham.com/articles.html) — correct
`https` links, short descriptions, guids, and clean Turbify chapter URLs.

<!-- BADGES:START -->

[![CI](https://github.com/wyattowalsh/paul-graham-essay-feeds/actions/workflows/ci.yml/badge.svg)](https://github.com/wyattowalsh/paul-graham-essay-feeds/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](./LICENSE)
[![Python](https://img.shields.io/badge/python-3.13%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/downloads/)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)

<!-- BADGES:END -->

## Quick start

```bash
mkdir pg-feeds && cd pg-feeds
uvx --from git+https://github.com/wyattowalsh/paul-graham-essay-feeds \
  pg-essay-feeds update
```

Writes `feeds/` (and `data/essays.json`) into the current directory. Point a
feed reader at the local files.

Default `update` enriches each essay (~1 HTTP GET per page, ~233 today). Use
`--no-enrich` (or `PG_ESSAY_FEEDS_ENRICH=false`) for a fast index-only run.

## What you get

| File | Format | Contents |
| --- | --- | --- |
| `feeds/rss.xml` | RSS 2.0 | title, link, guid, short description |
| `feeds/atom.xml` | Atom 1.0 | same, Atom shape |
| `feeds/feed.json` | JSON Feed 1.1 | same + short `summary` / `content_text` |
| `feeds/.manifest.json` | integrity | SHA-256 + sizes for the three feeds |
| `data/essays.json` | catalog | structured items (gitignored; not in manifest) |

**Not included:** full essay bodies, OPML, or a hosted site.

## Why not a bare scrape?

| Concern | Typical scrapes | This project |
| --- | --- | --- |
| Scheme | often `http://` | **`https://`** |
| Turbify chapters | `paulgraham.com/https://sep…` breakage | clean CDN URLs |
| Description | none | short summary (optional enrich) |
| Stable id | rare | **`guid` / JSON `id`** |
| Dates | invented day-1 | month+year → hint only; no feed date unless a real full day exists |
| Formats | RSS-ish only | **RSS + Atom + JSON** |

## Notebook (Colab / Jupyter)

[`notebook.ipynb`](./notebook.ipynb) — form UI + hidden code. **Run all** to
live-generate feeds (`uvx` fetch → parse → validate → write) and download
`feeds.zip`. Does **not** copy committed feed files from the repo.

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/wyattowalsh/paul-graham-essay-feeds/blob/main/notebook.ipynb)

1. Open the notebook in Colab  
2. Set options (output dir, enrich, link probes)  
3. **Runtime → Run all** → save `feeds.zip`  

## CLI

```bash
# index only (no per-page summary scrape)
pg-essay-feeds update --no-enrich

# offline HTML file
pg-essay-feeds update --source-file articles.html --no-enrich

# force rewrite even when index hash is unchanged
pg-essay-feeds update --force

# structural check
pg-essay-feeds check

# optional live HEAD/GET of every essay URL
pg-essay-feeds update --validate-links -v
```

CLI flags override env Settings only when explicitly passed. Full precedence:
[DOCS.md](./DOCS.md#cli-reference).

## Configuration

Environment prefix: `PG_ESSAY_FEEDS_` (pydantic-settings).

| Env var | Default |
| --- | --- |
| `PG_ESSAY_FEEDS_SOURCE_URL` | official `articles.html` |
| `PG_ESSAY_FEEDS_REPO_ROOT` | cwd |
| `PG_ESSAY_FEEDS_MIN_ITEMS` | `233` |
| `PG_ESSAY_FEEDS_TIMEOUT` | `30` |
| `PG_ESSAY_FEEDS_ENRICH` | `true` |
| `PG_ESSAY_FEEDS_VALIDATE_LINKS` | `false` |
| `PG_ESSAY_FEEDS_LINK_WORKERS` | `8` |

```bash
export PG_ESSAY_FEEDS_MIN_ITEMS=233
export PG_ESSAY_FEEDS_ENRICH=false   # optional: skip per-page scrapes
```

Full list (retries, workers, timeouts): [DOCS.md](./DOCS.md#configuration).

## Develop

Contributor docs (architecture, tests, CI): **[DOCS.md](./DOCS.md)**

```bash
uv sync --all-groups
just all    # lint + types + tests (≥90% cov) + check
```

## Notes

- Unofficial — not affiliated with or endorsed by Paul Graham.  
- No full essay bodies in feeds; short `description` / `summary` / JSON `content_text` only (same short `feed_summary()`, not the essay body). Copyright on essay text remains with the author.  
- Stable ids from URLs; Turbify chapters use a UUID derived from the path.  

## License

MIT — see [LICENSE](./LICENSE).
