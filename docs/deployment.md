# Deployment plan

## Target host: Vercel

Publish the assembled static directory (`public/`) containing:

- `index.html` (from `site/index.html`)
- `feeds/rss.xml`, `feeds/atom.xml`, `feeds/feed.json`, `feeds/subscriptions.opml`

Expected production base (configure to match your Vercel project):

```text
https://paul-graham-essay-feeds.vercel.app/
```

Subscription URLs:

```text
https://paul-graham-essay-feeds.vercel.app/feeds/rss.xml
https://paul-graham-essay-feeds.vercel.app/feeds/atom.xml
https://paul-graham-essay-feeds.vercel.app/feeds/feed.json
https://paul-graham-essay-feeds.vercel.app/feeds/subscriptions.opml
```

Derive self/feed/catalog links from `deployment.public_base_url` (or
`PG_ESSAY_FEEDS_PUBLIC_BASE_URL` / `--public-base-url`). Never emit placeholders.

### Vercel project settings

1. Import the GitHub repository into Vercel.
2. Leave **Root Directory** at the repository root (package is top-level).
3. Framework: Other; use `vercel.json` `buildCommand` + `outputDirectory`.
4. Optional: custom domain.

Local assemble:

```bash
bash scripts/assemble_public.sh
```

### Content-Type headers

Configured in `vercel.json` for RSS, Atom, JSON Feed, and OPML.

## Static index

`site/index.html` lists formats, relative feed links, source index, and the
unofficial-project disclaimer.

## Workflows

GitHub Actions at `.github/workflows/` (same root as the package):

| Workflow | Purpose |
|---|---|
| `.github/workflows/ci.yml` | Offline quality gates + fixture build |
| `.github/workflows/update-feeds.yml` | Scheduled/manual update → automation PR |
| `.github/workflows/deploy-vercel.yml` | Optional CLI deploy using Vercel secrets |

### Deploy modes

| Mode | When | Behavior |
|---|---|---|
| **A. Vercel Git integration** | Preferred production | Root Directory = repo root; platform runs `vercel.json` `buildCommand` → `public/` |
| **B. GHA `deploy-vercel.yml`** | Secrets configured | Local `assemble_public` is fail-fast smoke; `vercel --prod` deploys (not `--prebuilt`) |
| **C. Prebuilt (future)** | Optional | `vercel pull` → `vercel build` → `deploy --prebuilt` — not wired yet |

### Vercel CLI secrets (optional Mode B)

- `VERCEL_TOKEN`
- `VERCEL_ORG_ID`
- `VERCEL_PROJECT_ID`

Prefer Mode A (Git integration) when possible.

## Post-deploy checks

- Fetch every public feed URL over HTTPS.
- Confirm Content-Type headers.
- Confirm self/feed URLs match the deployed base when configured.
- Import OPML in at least one reader.
- Smoke RSS, Atom, and JSON Feed in representative clients.
