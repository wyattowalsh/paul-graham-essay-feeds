# Design decisions

## D-001: repository name

**Decision:** `paul-graham-essay-feeds`.

The full name is immediately searchable, avoids the ambiguity of `pg`, and
remains accurate across several feed formats. Singular `essay` is the compound
modifier for `feeds`.

## D-002: modern bounded format set

**Decision:** RSS 2.0, Atom 1.0, JSON Feed 1.1, and OPML 2.0 catalog only.

Legacy RSS dialects add complexity without meaningful current utility. OPML is
included as a subscription exchange format, not treated as an item-feed peer.

## D-003: metadata-only

**Decision:** publish titles, links, author metadata, and concise generated
summaries; do not republish essay bodies.

This minimizes copyright, sanitization, bandwidth, and change-detection risk.

## D-004: no fabricated publication dates

**Decision:** omit original publication fields. Persist feed-observation times
for state and Atom's required `updated` field.

The official index does not provide authoritative publication timestamps.

## D-005: one canonical model

**Decision:** every renderer consumes the same immutable canonical sequence.

This makes cross-format parity enforceable and prevents independent serializer
state from drifting.

## D-006: zero runtime dependencies by default

**Decision:** use Python's standard library unless a dependency clearly reduces
net complexity or improves correctness enough to justify operational cost.

Dev tooling may use pytest, Ruff, and ty.

## D-007: Vercel as intended hosting (supersedes Pages-primary)

**Decision:** host the static site and feeds on **Vercel**, with a configurable
`public_base_url` (example:
`https://paul-graham-essay-feeds.vercel.app/`).

GitHub Pages is no longer the primary deploy path. Prefer the Vercel Git
integration with Root Directory `paul-graham-essay-feeds`, or optional CLI
deploy via GitHub Actions secrets.

## D-008: reviewed automation changes

**Decision:** scheduled updates should create or refresh an automation pull
request rather than bypass validation or source-change review.

Direct commits may be enabled later for a proven stable workflow.

## D-009: stable Atom feed id

**Decision:** keep the default Atom feed `id` as the historical tag URI
`tag:wyattowalsh.github.io,2026:paul-graham-essay-feeds` even after the hosting
cutover to Vercel.

RFC 4287 requires that an Atom document's `atom:id` MUST NOT change when the
document is relocated, migrated, or republished. The feed id is independent of
`public_base_url` (self links may change; identity must not).
