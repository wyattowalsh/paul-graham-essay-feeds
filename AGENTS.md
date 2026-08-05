# Repository instructions

## Mission

Unofficial metadata-only RSS / Atom / JSON Feed for
https://paulgraham.com/articles.html. Local CLI (+ Colab notebook), with a
schema-versioned durable catalog. The GitHub repo (`feeds/` + `catalog.json`)
is the published product — no separate publish/site surface.

---

## Docs map

| Doc | Audience |
| :--- | :--- |
| [README.md](./README.md) | Users (Colab + local CLI + hosted feeds) |
| [DOCS.md](./DOCS.md) | Developers — **single SSOT** (architecture, CLI, CI, decisions) |
| [notebook.ipynb](./notebook.ipynb) | Public Colab — Run all → `feeds.zip` |

There is **no** `docs/` tree. Normative architecture decisions live in
[DOCS.md § Architecture decisions](./DOCS.md#architecture-decisions-normative).

---

## Target architecture

```text
raw fetch → decode → discover → catalog reconcile → refresh plan
  → fetch pages (enrich GET = check+summary; probe only non-enrich URLs)
  → FeedSnapshot (enriched + simple) → RSS/Atom/JSON ×2
  → deep verify → project feeds/ (6 files) + durable catalog
```

```text
catalog.json              # durable SSOT (repo root) — mirrors current index
feeds/rss.xml|atom.xml|feed.json                 # enriched
feeds/rss.simple.xml|atom.simple.xml|feed.simple.json  # simple (title/link)
# no site/*
```

---

## Layout

| Area | Responsibility |
| :--- | :--- |
| `src/paul_graham_essay_feeds/` | Domain package (~11 modules: cli, settings, pipeline, http, discover, enrich, catalog, feeds, verify, models, publication) |
| `tests/` | unit / integration / e2e / smoke / live / characterization |
| `DOCS.md` | Developer + architecture decision SSOT |

**Schema SSOT:** Pydantic models in `models.py`. No parallel JSON Schema tree. HTML via **selectolax**.

---

## Rules

| Area | Rule |
| :--- | :--- |
| Runtime | Python **3.12+** + `uv` |
| Tests | Offline default (`-m 'not live'`); coverage ≥90% on full suite; relative invariants only |
| Feed body | No full essay bodies; short source-derived summary only |
| HTTP | `trust_env=False`; hop-safe allowlist; HEAD ≠ body budget; raw-byte caps |
| Dates | month+year → `published_hint` only; no invented day-1 dates; no 1970 Atom sentinel |
| State | Durable **catalog** is SSOT; feeds are projections |
| Product | Repo `feeds/` + `catalog.json` — no `publish.py`, no `site/` |
| CLI | `update` + `check` only; flags override Settings only when explicit; quiet success → zero stdout+stderr; success-only side-channels (--result-file, $GITHUB_OUTPUT) allowed |
| Models | Every Field has a description; `extra="forbid"` where durable; aware UTC |
| Docs | Fold maintainer guidance into `DOCS.md` only — do not recreate `docs/` |

### Authorized

- Schema-versioned durable catalog (not flat `data/essays.json`)
- Deterministic flat feed projections under `feeds/` (enriched + simple)
- Configurable public base URL for feed self links

### Forbidden

- OPML · full essay bodies · invented publication dates · LLM summaries by default
- `site/` / `publish.py` / legacy pipeline CLI escape hatches
- Soft-retain / lifecycle / tombstone catalog states; feed subdirectories
- Parallel `docs/` or JSON Schema trees as second SSOT

---

## Gates

```bash
uv sync --locked --all-groups
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest --cov-fail-under=90
uv run pg-essay-feeds check --quiet
uv build --no-sources
```

Prefer `just all` / `just ci-local`. See [DOCS.md](./DOCS.md).
