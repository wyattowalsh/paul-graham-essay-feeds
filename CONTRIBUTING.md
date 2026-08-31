# Contributing

Unofficial metadata-only RSS / Atom / JSON Feed for
https://paulgraham.com/articles.html. The GitHub repo (`feeds/` +
`catalog.json`) is the published product.

## Before you start

Architecture, CLI, CI, and decisions live in **[DOCS.md](./DOCS.md)** (single
SSOT). There is no `docs/` tree. Agent instructions: [AGENTS.md](./AGENTS.md).
User subscribe path: [README.md](./README.md).

## Develop

Python **3.12+** and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --all-groups
just all
```

Default tests are offline (`-m 'not live'`). Do not hit paulgraham.com unless
you opt in (`just test-live` / `pytest -m live`).

Quality gates: ruff format + check, ty, pytest coverage ≥90% on the full
suite, `pg-essay-feeds check --quiet`.

## Product rules (short)

- CLI is `update` + `check` only. Quiet success is zero stdout and stderr.
- No `site/`, OPML, full essay bodies, invented day-1 dates, or LLM summaries.
- Catalog is SSOT; feeds are projections. Ship `check` and the seven product
  files together.
- Software is MIT; essay text and derived titles/summaries remain Paul
  Graham's — see [NOTICE](./NOTICE).

## Pull requests

Target `main`. Keep changes scoped. Do not cut release tags, push, or create
a GitHub Release from an agent session.

Security reports: [SECURITY.md](./SECURITY.md).
