# Repository instructions

## Mission

Unofficial metadata-only RSS / Atom / JSON Feed for
https://paulgraham.com/articles.html. Local CLI (+ Colab notebook).

---

## Docs map

| Doc | Audience |
| :--- | :--- |
| [README.md](./README.md) | Users (Colab hero CTA + local CLI) |
| [DOCS.md](./DOCS.md) | Developers |
| [notebook.ipynb](./notebook.ipynb) | Colab / Jupyter — try without install |

---

## Layout (8 domain modules)

| Module | Responsibility |
| :--- | :--- |
| `model.py` | `Essay`, constants, URL helpers, Atom sentinel |
| `settings.py` | pydantic-settings (`PG_ESSAY_FEEDS_*`) |
| `fetch.py` | `hop_safe_request` (+ `hop_safe_get`) + Tenacity |
| `validate.py` | structural (via extract) + optional live probes |
| `extract.py` | index HTML → essays → structural validate |
| `enrich.py` | per-page short summary; month+year → `published_hint` only |
| `feeds.py` | render RSS/Atom/JSON + atomic write + verify |
| `cli.py` | Typer + logging (loguru/rich); `check` → `verify_feed_artifacts` |

```text
# artifacts (not package code):
feeds/             # rss.xml, atom.xml, feed.json
notebook.ipynb     # Colab/Jupyter: live-generate + download
```

---

## Rules

| Area | Rule |
| :--- | :--- |
| Runtime | Python 3.13 + `uv` |
| Deps | typer, httpx, pydantic, pydantic-settings, tqdm, loguru, rich, tenacity |
| Tests | No network in default tests (`-m 'not live'`); coverage fail-under 90%; assert relative invariants (`min_items` / count parity), never a fixed live catalog size |
| Feed body | No full essay bodies; short `feed_summary()` only (JSON `content_text` = same short text) |
| HTTP | httpx `trust_env=False`; `hop_safe_request` with start-bound `allow_loopback`; redirect close without body read; final stream + Content-Length reject; HEAD probes use same `max_bytes` budget |
| Dates | month+year → `published_hint` only; enrich never invents day-1 `published_at`; feed dates only when `published_at` is set |
| Writes | stage temps → `os.replace` the three feeds; `verify_feed_artifacts` hard-checks count parity + JSON `content_text == summary` + length cap |
| Hash skip | `index_hash` + item fingerprint in `feed.json` `_pg_essay_feeds` → no-op update when index unchanged |
| CLI | flags override Settings only when explicitly passed (`ParameterSource`) |
| Workers | defaults `enrich_workers=4`, `link_workers=4` (env override OK) |
| Models | every Field has a description; keep annotations complete |

> [!IMPORTANT]
> Do **not** reintroduce OPML, Vercel/site, public-base-url,
> `feeds/.manifest.json`, or `data/essays.json`.

---

## Unit tests

Flat mirror of package modules:

```text
tests/unit/test_<module>.py  ↔  src/paul_graham_essay_feeds/<module>.py
```

Examples: `test_feeds.py` covers render / write / verify; `test_validate.py`
covers live + structural probes. No nested
`tests/unit/paul_graham_essay_feeds/` unless the package gains subpackages.

---

## Gates

```bash
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest
```

> [!TIP]
> Prefer `just all` locally (lint + types + tests + `pg-essay-feeds check`).
> See [DOCS.md](./DOCS.md) for architecture, CLI, and feed contracts.
