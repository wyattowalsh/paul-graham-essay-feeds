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
| [README.md](./README.md) | Users (Colab + local CLI + hosted feeds) |
| [DOCS.md](./DOCS.md) | Developers |
| [docs/adr/](./docs/adr/) | Architecture decision records |
| [notebook.ipynb](./notebook.ipynb) | Colab / Jupyter — pin immutable ref |

---

## Target architecture

```text
raw fetch → decode → discovery → catalog reconcile → refresh plan
  → prior-good enrich → FeedSnapshot → RSS/Atom/JSON
  → deep verify → immutable generation + manifest
  → atomic current pointer → optional site/
```

```text
state/generations/<id>/{catalog.json,feeds/*,reports/*,manifest.json}
state/current.json
feeds/*   # migration projections only
site/*    # Pages artifact from validated current
```

---

## Layout

| Area | Responsibility |
| :--- | :--- |
| `src/paul_graham_essay_feeds/` | Domain package (transport, decode, discovery, catalog models, enrich, render, verify, publish, CLI) |
| `docs/adr/` | Normative ADRs |
| `tests/` | unit / integration / e2e / smoke / live / characterization |

**Schema SSOT:** Pydantic models (e.g. `catalog_models.py`). No parallel JSON Schema tree.

---

## Rules

| Area | Rule |
| :--- | :--- |
| Runtime | Python **3.12+** + `uv` |
| Tests | Offline default (`-m 'not live'`); coverage ≥90%; relative invariants only |
| Feed body | No full essay bodies; short source-derived summary only |
| HTTP | `trust_env=False`; hop-safe allowlist; HEAD ≠ body budget; raw-byte caps |
| Dates | month+year → `published_hint` only; no invented day-1 dates; no 1970 Atom sentinel |
| State | Durable **catalog** is SSOT; feeds are projections |
| Publish | Verify in memory → generation + manifest → one atomic current pointer |
| CLI | Flags override Settings only when explicit; quiet success → zero bytes |
| Models | Every Field has a description; `extra="forbid"` where durable; aware UTC |

### Authorized

- Schema-versioned durable catalog (not flat `data/essays.json`)
- Generation-scoped deterministic manifest + single-pointer publication
- Minimal static host (`site/`) + configurable public base URL

### Forbidden

- OPML · full essay bodies · invented publication dates · LLM summaries by default

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

Prefer `just all` / `just ci-local`. See [DOCS.md](./DOCS.md) and ADRs.
