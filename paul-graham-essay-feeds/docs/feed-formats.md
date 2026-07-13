# Feed format mapping

## Authoritative specifications

- RSS 2.0: <https://www.rssboard.org/rss-specification>
- Atom 1.0: <https://www.rfc-editor.org/rfc/rfc4287>
- JSON Feed 1.1: <https://www.jsonfeed.org/version/1.1/>
- OPML 2.0: <https://2005.opml.org/spec2.html>

## Canonical feed metadata

| Canonical field | Value |
|---|---|
| Title | `Paul Graham: Essays` |
| Description | Unofficial metadata feeds ordered from the official index |
| Author | Paul Graham |
| Author URL | `https://paulgraham.com/` |
| Home/alternate URL | `https://paulgraham.com/articles.html` |
| Language | `en` (`en-us` only where a format expects that convention) |
| Public feed URLs | Derived from configured deployment base URL |

## Item mapping

| Canonical field | RSS 2.0 | Atom 1.0 | JSON Feed 1.1 |
|---|---|---|---|
| `title` | `item/title` | `entry/title` | `title` |
| `url` | `item/link` | alternate `link@href` | `url` |
| `stable_id` | `guid` | `entry/id` | `id` |
| author | `dc:creator` | `author/name` | top-level `authors` |
| `last_changed_at` | omitted | required `entry/updated` | omitted |
| content | concise description | concise summary | required non-empty `content_text` |

## RSS 2.0 decisions

- Keep the existing audited GUID behavior.
- Internal canonical URLs are permalink GUIDs.
- External Turbify resources use stable UUID URNs with `isPermaLink="false"`.
- Omit item `pubDate` because no authoritative publication timestamp is present.
- Include `atom:link rel="self"` only when the deployed URL is known.

## Atom 1.0 decisions

Atom requires `updated` on both feed and entries. Entry `updated` is canonical
`last_changed_at`, explicitly documented as feed-observation metadata. Feed
`updated` is the newest significant logical build time and does not churn on
no-op checks.

Use:

- a stable configured or deterministic feed `id`;
- an alternate link to the official index;
- a self link when deployed;
- per-entry author, title, ID, updated, alternate link, and summary.

Do not add `published`.

## JSON Feed 1.1 decisions

JSON Feed requires `items`, a unique string `id` per item, and at least one of
`content_text` or `content_html`. Use metadata-only `content_text`, for example:

```text
Read “How to Do Great Work” by Paul Graham at the official source:
https://paulgraham.com/greatwork.html
```

Use `home_page_url` always and `feed_url` only when deployed. Omit publication
and modification dates to avoid confusing feed-observation timestamps with essay
metadata.

## OPML 2.0 decisions

OPML is a subscription catalog, not an essay-entry format.

- Root: `<opml version="2.0">` with required head and body.
- RSS and Atom: subscription outlines with `type="rss"` and `xmlUrl`.
- JSON Feed: link outline with `type="link"` and `url`.
- Every outline has required `text`.
- Generation requires real public URLs. Never emit placeholders.

## Content types

| File | Recommended content type |
|---|---|
| `rss.xml` | `application/rss+xml; charset=utf-8` |
| `atom.xml` | `application/atom+xml; charset=utf-8` |
| `feed.json` | `application/feed+json; charset=utf-8` |
| `subscriptions.opml` | `text/x-opml; charset=utf-8` |
