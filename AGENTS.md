# Repository instructions

## Mission

Unofficial metadata-only RSS / Atom / JSON Feed for
https://paulgraham.com/articles.html. Local CLI (+ optional notebook).

## Docs map

- **Users:** [README.md](./README.md)
- **Developers:** [DOCS.md](./DOCS.md)
- **Notebook:** [notebook.ipynb](./notebook.ipynb)

## Layout (8 domain modules)

```text
model.py           # Essay, constants, URL helpers, Atom sentinel
settings.py        # pydantic-settings (PG_ESSAY_FEEDS_*)
fetch.py           # hop_safe_request (+ hop_safe_get) + Tenacity
validate.py        # structural (via extract) + optional live probes
extract.py         # index HTML → essays → structural validate
enrich.py          # per-page short summary; month+year → published_hint only
feeds.py           # render RSS/Atom/JSON + atomic write + verify
cli.py             # Typer + logging (loguru/rich); check → verify_feed_artifacts

# artifacts (not package code):
feeds/             # rss.xml, atom.xml, feed.json, .manifest.json
data/essays.json   # gitignored catalog (not in manifest)
notebook.ipynb     # Colab/Jupyter: live-generate + download
```

## Rules

- Python 3.13 + `uv`
- Runtime: typer, httpx, pydantic, pydantic-settings, tqdm, loguru, rich, tenacity
- No network in default tests (`-m 'not live'`)
- Coverage fail-under 90%
- No full essay bodies in feeds; short `feed_summary()` only (JSON `content_text` = same short text)
- httpx: `trust_env=False`; `hop_safe_request` with start-bound `allow_loopback`; redirect close without body read; final stream + Content-Length reject; HEAD probes use same `max_bytes` budget
- Dates: month+year → `published_hint` only; enrich never invents day-1 `published_at`; feed dates only when `published_at` is set
- Writes: stage temps → `os.replace` feeds → `feeds/.manifest.json` last; `verify_feed_artifacts` hard-checks parity + `content_text` + manifest hashes
- Hash skip: `index_hash` + item fingerprint no-op update; page `content_hash` reuses enrich parse
- CLI flags override Settings only when explicitly passed (`ParameterSource`)
- Pydantic models: every Field has a description; keep annotations complete
- Do not reintroduce OPML, Vercel/site, or public-base-url product surface

## Gates

```bash
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest
```
