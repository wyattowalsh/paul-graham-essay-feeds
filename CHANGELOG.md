# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **PGF-P0-001:** Catalog-only `state_changed` now re-checks material after the
  writer lock and recover (including recover-false). Matching disk overlays clocks
  onto the reloaded catalog; divergence publishes this run's feeds and catalog in
  the same lock. The pre-lock object is never catalog-only-saved.
- **PGF-P1-002:** The installed `pg-essay-feeds` / `python -m` boundary maps
  parser usage and `ConfigurationError` to exit 1 with concise stderr (Click's
  default usage exit 2 no longer leaks).
- **PGF-P1-003:** Schema-v2 clocks are one meaning everywhere: `last_checked_at`
  is the attempt clock (synced with `last_attempted_at`); planner freshness uses
  `last_success_at` only; accepted index observations persist a complete
  `ResourceState`.
- **PGF-P1-004:** Notebook `AUTO_DOWNLOAD` gates Colab download; status panel
  aggregates `PGF_REACHABILITY_FAIL` and `PGF_ENRICH_DEGRADED` from probes and
  enrichment GETs (parse-after-HTTP is degradation, not unreachable).
- **PGF-P2-005:** DOCS distinguish the public seven-file product from authorized
  private `.cache` staging (forbidden is a public generation tree / `current.json`).
- **RV-C-001:** After recover rematerializes a generation, catalog-only writes
  re-check post-recover disk and never save the pre-recover catalog (clock overlay
  onto the reloaded catalog, or full publish when material now differs).
- Characterization lock: truncated/unclosed XML on CLI `check` is `UNPARSEABLE_XML` (exit 2), not a fake item count.
- CI offline smoke now passes `--no-validate-links` (matches `just smoke` / DOCS).
- Simple Atom feed `<id>` is `FEED_ID_SIMPLE` (golden + committed `feeds/atom.simple.xml`).
- `assert_verified` and in-memory `update` verify thread `kind`, so simple-triple
  parse failures label `feeds/*.simple.*` instead of `feeds/rss.xml`.
- `verify_feed_dir` accepts `kind` (no enriched-only footgun).
- `state_changed` catalog-only writes take the writer lock and honor recover
  before `save_catalog` (RV-R-001).
- Unexpected CLI exceptions map to exit 4 via `exit_code_for_exception` (AD-006).
- `check` help/docs: `catalog.json` is required (M-25), not optional / "when present".

### Changed

- On-disk `catalog.json` migrated to `schema_version: 2` (offline projection regen).

## [0.1.0] - 2026-08-21

### Fixed

- **RV-R-001:** Publication recover runs only under the writer lock (no pre-lock
  `recover_materialize` race window).
- **RV-R-002:** Atom feed ids selected via `FeedSnapshot.variant`, not a
  `"simple" in feed_url` substring heuristic.
- **RV-R-003 / RV-R-006:** Staged `MANIFEST.json` digests re-verified before
  public materialize; materialize pointer writes use atomic text writes.
- **RV-R-004:** Page (and index when transport provides raw) `raw_sha256` /
  `decoded_sha256` stay distinct; enrich evidence carries both digests.
- **RV-R-005:** `settings.host_cooldown_seconds` wired through `HostCooldown`
  into enrich and live link probes (default still 0).
- **RV-R-007:** Discovery anomaly quarantine uses true stable-id set overlap
  (same-size total swap quarantines).
- **RES-H09:** Position-only reorder is not material (does not bump
  `observed_updated_at` / material `updated` membership).
- **RES-H06:** Cross-format title/url/summary payload parity in deep verify.
- **RV-R-008:** `--from-feeds` docs/help match in-memory bootstrap (H-12).
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
