# Design

Visual and brand spec for Paul Graham Essay Feeds. Architecture, CLI, and CI
remain in [`.github/DOCS.md`](./.github/DOCS.md). This file is not a second
architecture SSOT.

Source assets live in [`assets/brand/`](./assets/brand/). GitHub Pages copies
only the flat overlay in [`assets/brand/site/`](./assets/brand/site/). There is
no `docs/` tree and no committed `site/` product.

## Thesis

The hosted surface is a **subscribe landing**, not a magazine and not a gallery
of twelve equivalent feeds.

1. **Simple RSS** is the primary action (title and link only).
2. **Full catalog** is the rest of the choice: RSS / Atom / JSON Feed × Simple
   or Enriched.
3. The cinematic night scene is for **social cards and the README hero**. The
   landing uses paper, ink, and the square mark.

## Why `/latest/` is not on the landing

The subscribe choice is two axes:

| Axis | Values | Meaning |
| :--- | :--- | :--- |
| Format | RSS, Atom, JSON Feed | reader format |
| Variant | Simple, Enriched | title-only vs short source excerpt |

`/latest/*` is a **window**, not a third catalog. It keeps the newest 20 items,
rewrites feed identity (title, self URL, Atom id `:latest`), and leaves item
ids as the ordered prefix of the full feed (PGF-FINAL-001).

It still exists for readers who already want a bounded window. The landing and
the README primary path do not present it as a peer of Simple vs Enriched.
Generating it without promoting it avoids a one-time unsubscribe for anyone
already pointed at those URLs.

## Color

From `assets/brand/brand-tokens.json`:

| Token | Hex | Role |
| :--- | :--- | :--- |
| `night` | `#061827` | Dark paper, light-mode CTA, theme-color |
| `night_border` | `#173A49` | Dark rules |
| `emitter_green` | `#39FF70` | Accent hairline, dark-mode mark |
| `micro_emitter_green` | `#52FF7F` | 16 px favicon only |
| `warm_paper` | `#F4EFE4` | Light paper, dark-mode ink, CTA label on navy |

Landing CSS exposes the same values as `--pg-feeds-night`,
`--pg-feeds-night-border`, `--pg-feeds-emitter`, and `--pg-feeds-paper`.

Emitter green on warm paper fails body-text contrast. Light-mode interactive
color is navy (`night`) on paper. Dark-mode CTA is emitter on navy.

## Type

| Role | Stack |
| :--- | :--- |
| Display | Iowan Old Style, Palatino, Book Antiqua, Georgia, serif |
| Body | Charter, Sitka Text, Iowan Old Style, Georgia, serif |
| Mono | SFMono-Regular, ui-monospace, Menlo, Consolas |

No webfonts. No third-party font files. The artwork does not imply affiliation
with Paul Graham.

## Landing

`src/paul_graham_essay_feeds/pages.py` generates one `index.html`.

| Region | Content |
| :--- | :--- |
| Skip link | First focusable control → `#feeds` |
| Header | 56 px `favicon.svg` (decorative) + kicker + title |
| Start here | Simple RSS CTA; Atom / JSON Feed as secondary text links |
| Full catalog | Three format cards, Simple then Enriched |
| Footer | Root vs `/feeds/` byte identity; `/latest/` mentioned, not linked |

Head includes favicon, Apple touch icon, `site.webmanifest`, Simple
`rel="alternate"`, Open Graph, and `twitter:card` = `summary_large_image`.
Icon hrefs are relative (GitHub Pages subpath). `og:image` is absolute.

Layout: one column until ~40 rem / container 32 rem, then three format cards.
`prefers-reduced-motion` disables transitions. Focus-visible ring uses `--mark`.

## Asset map

Suggested alt text for cinematic crops:

> A green dot-matrix pedestrian signal broadcasts a dotted RSS symbol and three floating essay pages toward a seated classical statue at night.

| Use | Path |
| :--- | :--- |
| GitHub social preview (upload in repo settings) | `assets/brand/social/github-social-preview-1536x768.jpg` |
| Open Graph | `assets/brand/social/open-graph-1200x630.jpg` (also in `site/`) |
| README hero | `assets/brand/web/readme-hero-1536x768.webp` (+ AVIF) |
| Browser favicon | `assets/brand/favicon/favicon.ico` + `favicon.svg` |
| 16 px geometry | `assets/brand/marks/mark-micro.svg` |
| Canonical square mark | `assets/brand/marks/mark-canonical.svg` |
| Apple touch icon | `assets/brand/app/apple-touch-icon.png` |
| Web-app icons | `assets/brand/app/app-icon-192x192.png`, `app-icon-512x512.png` |
| Maskable icons | `assets/brand/app/app-icon-maskable-192x192.png`, `512×512` |
| JSON Feed `icon` / `favicon` | `feed-icon-512x512.png`, `feed-favicon-64x64.png` on Pages |
| Atom `icon` / `logo` | `atom-icon-144x144.png`, `atom-logo-512x256.png` |
| RSS channel `image` | `rss-channel-image-144x144.png` |

Checksums: [`assets/brand/SHA256SUMS`](./assets/brand/SHA256SUMS). Inventory:
[`assets/brand/manifest.json`](./assets/brand/manifest.json).

## Favicon policy

The favicon is not a downscaled cinematic crop.

- **Below 24 CSS px:** dedicated micro pedestrian; no RSS arcs.
- **32 px and above:** canonical pedestrian plus two-arc RSS construction.
- `favicon.ico` has distinct 16, 32, and 48 px entries.
- Opaque navy tile; no separate light/dark favicons.

## Pages overlay

`assemble_pages()` copies `assets/brand/site/` onto `_site/` and fails closed if
that directory is missing or the file set drifts. QA previews, source JPEGs,
and social masters stay in git; they are not uploaded.

Deployed brand files:

`favicon.ico`, `favicon.svg`, `favicon-48x48.png`, `favicon-96x96.png`,
`apple-touch-icon.png`, `site.webmanifest`, `app-icon-192x192.png`,
`app-icon-512x512.png`, `app-icon-maskable-192x192.png`,
`app-icon-maskable-512x512.png`, `feed-icon-512x512.png`,
`feed-favicon-64x64.png`, `open-graph-1200x630.jpg`,
`rss-channel-image-144x144.png`, `atom-icon-144x144.png`,
`atom-logo-512x256.png`.

`verify_pages_artifact` requires those files, the six feed copies, `/feeds/`
aliases, `/latest/*`, `index.html`, and `.nojekyll`. The landing must not
`href` `/latest/`.

## Feed graphics

RSS `<image>`, Atom `icon`/`logo`, and JSON Feed `icon`/`favicon` are emitted
only when `FeedSnapshot.public_base_url` is set. Offline/local generation does
not invent public URLs. `/latest/` slicing keeps those header fields.

Hosted URLs are the Pages root files (not under `/latest/`).

## Accessibility

- Skip link, `<main>`, labeled sections, 2.75 rem CTA, 2.5 rem card links.
- Decorative header mark: empty `alt`.
- Cinematic images: the alt sentence above.
- Light CTA: navy on paper. Dark CTA: navy on emitter.
- Reduced motion: transitions collapsed.

## Provenance

- Archival source: `assets/brand/source/social-preview-final-original-1536x768.jpeg`
  (1536×768, SHA-256 `fa02733351b461d5f6d62874a0e89137c9b45a05ee8d9fffa92c9b6b457596df`).
- Cinematic derivatives: deterministic crop, resize, sRGB, metadata stripped.
- Square identity is purpose-built vector, not a photographic shrink.
- No third-party fonts, logos, or official Paul Graham brand assets.

## Regenerating

Maintainer-only. Pillow and CairoSVG are not runtime package dependencies.

```bash
uv run --with pillow --with cairosvg \
  python tools/branding/render_assets.py \
  --source assets/brand/source/social-preview-final-original-1536x768.jpeg \
  --out dist/brand-assets/paul-graham-essay-feeds-brand-assets-complete
```

Then copy the refreshed `assets/brand/` tree back into the repository.

## GitHub social preview

Git does not set the repository social image. Upload
`assets/brand/social/github-social-preview-1536x768.jpg` in GitHub repository
settings → Social preview.
