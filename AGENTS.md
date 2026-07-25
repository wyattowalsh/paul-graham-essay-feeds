# Repository instructions

## Mission

Unofficial metadata-only RSS / Atom / JSON Feed for
https://paulgraham.com/articles.html. Local CLI (+ Colab notebook), with a
schema-versioned durable catalog, immutable generation publication, and optional
static hosted endpoints.

---

## Docs map

| Doc | Audience |
| :--- | :--- |
| [README.md](./README.md) | Users (Colab hero CTA + local CLI + hosted feeds) |
| [DOCS.md](./DOCS.md) | Developers |
| [docs/adr/](./docs/adr/) | Architecture decision records |
| [audit/](./audit/) | Program evidence, baseline, execution ledger |
| [notebook.ipynb](./notebook.ipynb) | Colab / Jupyter — pin immutable ref |

---

## Target architecture

```text
raw fetch evidence → decode → discovery+diagnostics → catalog reconcile
  → refresh plan → prior-good enrich → FeedSnapshot
  → RSS/Atom/JSON → deep verify → immutable generation+manifest
  → atomic current pointer → post-verify → optional site/
```

Canonical state (semantics fixed; exact paths per ADR-005):

```text
state/generations/<id>/{catalog.json,feeds/*,reports/*,manifest.json}
state/current.json
feeds/*            # compatibility projections during migration
site/*             # Pages artifact rebuilt from validated current
```

---

## Layout

| Area | Responsibility |
| :--- | :--- |
| Domain modules under `src/paul_graham_essay_feeds/` | fetch, decode, discovery, catalog, enrich, render, verify, publish, CLI |
| `docs/adr/` | Normative contracts (feed, catalog, time, HTTP, publication, CLI, governance, CI) |
| `schemas/` | JSON Schemas for catalog, feed snapshot, manifest |
| `tests/` | unit / integration / e2e / smoke / live / characterization / property / fault |
| `audit/` | baseline evidence + execution ledger (no secrets) |

---

## Rules

| Area | Rule |
| :--- | :--- |
| Runtime | Python **3.12+** (3.12 / 3.13 / 3.14) + `uv` |
| Deps | typer, httpx, pydantic, pydantic-settings, tqdm, loguru, rich, tenacity |
| Tests | Default offline (`-m 'not live'`); coverage fail-under 90%; relative invariants only |
| Feed body | **No full essay bodies**; short source-derived summary only |
| HTTP | httpx `trust_env=False`; hop-safe host allowlist; redirect close without body; raw-byte caps; HEAD ≠ body budget |
| Dates | month+year → `published_hint` only; never invent day-1 `published_at`; no 1970 semantic sentinel for Atom `updated` |
| State | Schema-versioned durable **catalog** is SSOT; feeds are projections |
| Publish | Verify in memory → immutable generation + manifest → **one** atomic current pointer |
| Determinism | Identical logical state → byte-identical canonical artifacts |
| Hosting | Optional public base URL; Atom/RSS self links + JSON `feed_url` when configured |
| CLI | Flags override Settings only when explicitly passed; quiet success → **zero** bytes |
| CI | Full-SHA action pins; least privilege; network jobs never hold write tokens; **zero** workflow warnings |
| Models | Every Field has a description; strict validation; aware UTC |

### Authorized (this program)

- Schema-versioned durable catalog (not flat `data/essays.json` SSOT)
- Generation-scoped deterministic manifest + single-pointer publication
- Minimal static hosted surface (`site/`) + configurable public base URL

### Still forbidden

- OPML
- Full essay body persistence or republication
- Invented publication dates
- Browser automation / LLM summaries by default
- Service/queue split without measured need

---

## Gates

```bash
uv sync --locked --all-groups
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest
uv run pg-essay-feeds check --quiet
uv build --no-sources
```

Prefer `just all` / `just ci-local` when available. See [DOCS.md](./DOCS.md) and ADRs.
