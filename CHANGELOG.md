# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **C-01 / L-14:** Catalog-only freshness updates (post-enrich material noop) now
  report `action=state_changed` with `changed_paths=("catalog.json",)` instead of
  `unchanged`. The scheduled `update-feeds.yml` workflow uploads and commits when
  `action` is `updated` **or** `state_changed`, so page-clock advances are no
  longer discarded by automation. CLI non-quiet copy distinguishes no durable
  write (`UNCHANGED`), catalog-only write (`STATE`), and material feed rewrite
  (`UPDATED`).
- **C-02 / M-02 / M-20 / M-23:** Resource lifecycle clocks distinguish attempts
  from successful validation. Failed enrich (timeout/5xx/parse) increments
  `failure_count` and sets `next_retry_at` without advancing the success TTL.
  Future success clocks are treated as stale. Missing summaries respect failure
  backoff. Catalog schema version is **2** with an idempotent 1→2 migration.
- **C-03 / M-01:** Transport failures raise retryable `httpx.TransportError`
  (not permanent `FeedError`) until Tenacity exhausts attempts. Wait policy
  honors bounded `Retry-After` (delta-seconds / HTTP-date, max 120s).
- **H-04 / H-17:** Catalog relational invariants fail closed (order↔entries
  bijection, key/`stable_id` match, unique positions, blank URLs). Feed
  projection no longer silently omits undated entries.
- **H-13 / H-16 / M-04:** `max_page_fetches` / `max_link_validations` Settings
  are wired; fair rotating cursor persists in `catalog.versions`; probes are
  independently capped. Workflow sets daily caps.
- **H-03 / M-06–08:** Discovery anomaly quarantine before hard-delete reconcile.
- **H-05 / M-25 / L-03:** Verifier rejects empty item ids and oversized
  artifacts; `check` requires `catalog.json`.
- **H-01 / H-02 / M-21–22:** Locked staged publication under `.cache/generations`
  with recover-on-open materialize pointer (public paths stay flat).
- **H-12:** `--from-feeds` bootstraps in memory only (no early catalog overwrite).
- **H-14 / H-15 / M-24:** Settings URL validation; centralized `feed_self_url`;
  distinct simple vs enriched Atom feed ids.

### Changed

- Pipeline machine side-channel action vocabulary is now
  `unchanged | state_changed | updated` (see `PipelineAction` and DOCS CLI
  contract). External consumers that only handled `updated|unchanged` should
  treat `state_changed` like a publishable catalog update.
- Durable `catalog.json` is written as `schema_version: 2`. Version 1 files are
  migrated on load (resource lifecycle clocks + migration_history entry).
- New module `publication.py` for writer lock + staged materialize.
