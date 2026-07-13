# Paul Graham Essay Feeds

Unofficial **metadata-only** feeds for every item on Paul Graham’s essays index:

- Source: <https://paulgraham.com/articles.html>
- Formats: RSS 2.0, Atom 1.0, JSON Feed 1.1, OPML 2.0
- Order: newest → oldest

**Unofficial.** Not affiliated with or endorsed by Paul Graham.

## Subscribe

| Format | URL |
|---|---|
| RSS | https://paul-graham-essay-feeds.vercel.app/feeds/rss.xml |
| Atom | https://paul-graham-essay-feeds.vercel.app/feeds/atom.xml |
| JSON Feed | https://paul-graham-essay-feeds.vercel.app/feeds/feed.json |
| OPML | https://paul-graham-essay-feeds.vercel.app/feeds/subscriptions.opml |

## Generate with `uvx` (no clone)

```bash
mkdir pg-feeds && cd pg-feeds
uvx --from git+https://github.com/wyattowalsh/paul-graham-essay-feeds \
  pg-essay-feeds update
```

Writes `feeds/`, `data/`, `reports/`, and `SHA256SUMS` into the current directory.

## Local install

Requires Python 3.13+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --all-groups
uv run pg-essay-feeds update          # live fetch
uv run pg-essay-feeds check
uv run pg-essay-feeds --help
```

Offline / CI fixture:

```bash
uv run pg-essay-feeds update \
  --source-file fixtures/articles-2026-07-11.fragment.html \
  --force
```

## Safety

- Newest-prefix additions accepted automatically.
- Removals, reorders, and mid-history inserts fail closed (override flags exist).
- Stdlib HTTP only; host allowlists; no-op updates preserve feed bytes and mtimes.
- No essay bodies; no fabricated publication dates.

## Develop

```bash
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest
```

## Deploy

Vercel serves committed files under `feeds/` via `vercel.json` (Root Directory =
repository root). Optional GitHub Action: `.github/workflows/deploy-vercel.yml`.

## License

MIT — see [LICENSE](./LICENSE).
