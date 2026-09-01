# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- GitHub Pages (`https://wyattowalsh.github.io/paul-graham-essay-feeds/`)
  is the canonical subscribe origin: committed `feeds/` plus a `/latest/*`
  projection of the newest 20 items. GitHub raw remains `text/plain` fallback.
- `--debug` prints unexpected-error tracebacks; `--allow-bootstrap-fallback`
  is required for discovery fallback when no prior catalog exists.
- Release artifacts include an sdist smoke test, `requirements.txt`, and a
  CycloneDX SBOM (`bom.cdx.json`).

### Fixed

- Parse-failed page validators no longer authorize a later HTTP 304 success.
- `check` treats the catalog as the feed oracle (title/URL/summary/id).
- Gzip/deflate decode aborts at the decoded-size cap instead of inflating
  first. HTTP 204/206 are not successful page/index bodies.
- Missing catalog after recover with a planned revision is a stale finalize.
- Staging manifest binds directory name, `gen_id`, and `last_generation_id`.


- **PGF-2026-030:** Crash recovery no longer skips `state_revision`
  compare-and-swap. Recover rematerializes first, then revision comparison
  always runs; a stale candidate aborts and must re-run. (Unreleased CAS was
  briefly labeled `PGF-2026-022`; that id remains the extraction-quality work
  in `[1.0.0]`.)
- **PGF-2026-039:** Missing `catalog.json` after materialize raises instead
  of returning an in-memory stand-in. Hatch sdist excludes `.grok/`.
- **PGF-2026-023:** Parse-failed HTTP 200 persists ETag, Last-Modified,
  hashes, byte counts, and encoding; success TTL and prior-good stay put.
- **PGF-2026-024:** Every previously present id needs two successful index
  observations before hard-delete (5+ first-run mass-delete removed).

### Changed

- **PGF-2026-032:** DOCS no longer prescribe `conditions.file_path` branch
  rulesets (not available on this public user-owned repo).
- **PGF-2026-033:** `release.yml` requires the tagged SHA to be an ancestor
  of `origin/main` (blobless fetch; not `--depth=1`).
- **PGF-2026-034:** Release quality/build copies CI feed-format contracts,
  offline pipeline smoke, and `py.typed` wheel assertion.
- **PGF-2026-025:** Tag `release.yml` enforces the same raw coverage.xml
  floor as CI.
- **PGF-2026-026:** DOCS ruleset snippets cover required checks plus a `v*`
  tag ruleset (maintainer-apply only; no path exception).
- **PGF-2026-028:** DOCS distinguish writer crash recovery, local reader
  mix, and Git commit atomicity.
- **PGF-2026-029:** Release job attests wheel/sdist and attaches SHA-256
  checksums plus a CycloneDX SBOM.
- **PGF-2026-036:** Optional `brotli` extra; CI job runs the Brotli decode
  path. Missing-brotli fail-closed unit test is unchanged.

## [1.0.0] - 2026-08-31

Ready for `v1.0.0` (tag not cut in this change). First coherent major after
the 2026-08-31 audit (`PGF-2026-*`). Historical `[0.2.0]` remains the prior
advertised-but-untagged integrity work.

### Added

- **PGF-2026-019:** Concise [SECURITY.md](SECURITY.md) and
  [CONTRIBUTING.md](CONTRIBUTING.md) at the repo root (point at DOCS.md;
  no `docs/` tree).
- **PGF-2026-020:** [NOTICE](NOTICE) plus LICENSE-adjacent README scope:
  software is MIT; essay titles, URLs, and derived summaries remain Paul
  Graham's. MIT does not relicense third-party text.
- **PGF-2026-021:** README leads with one-click Subscribe links for the six
  raw GitHub feeds (simple first, enriched second). Colab moved under
  maintainer / custom generation. A reader can subscribe without Python.

### Changed

- **PGF-2026-004:** Package `__version__` is `1.0.0`. README and notebook
  do not pin a git tag that does not exist. Intended release is **1.0.0**;
  until the `v1.0.0` tag exists, install from `main`.
- **PGF-2026-006:** Package classifiers are POSIX/macOS (not OS Independent).
  Writer lock is POSIX `fcntl.flock` only; no Windows lock.
- **PGF-2026-014:** Default `max_page_fetches` / `max_link_validations` are
  40 (matching CI). `--all-pages` / `PG_ESSAY_FEEDS_ALL_PAGES` is the
  explicit unlimited opt-in.
- Privileged publish / verify-product / release jobs stop forcing
  `setup-uv` `enable-cache: true` (safer default / explicit disable).

### Fixed

- **PGF-2026-001:** Writer lock release no longer unlinks `.cache/write.lock`.
  The inode stays stable so a waiter on the old fd cannot share exclusive
  ownership with a newly created path.
- **PGF-2026-002:** Finalize attaches the durable catalog material digest the
  candidate was based on. Under the writer lock, a slower older candidate
  aborts with `FeedError` instead of publishing over a newer accepted state.
- **PGF-2026-003:** Staging allocates `gen_id`, stamps
  `catalog.last_generation_id`, then writes artifacts + MANIFEST so
  manifest, pointer, and public catalog agree.
- **PGF-2026-005:** `validate_links=true` runs as an independent planned phase
  even on ordinary no-op/skip-network. `PipelineResult` and CLI `--result-file`
  expose `links_checked` / `links_skipped`.
- **PGF-2026-007:** Coverage report precision is 2 with `fail_under = 90`.
  89.955% no longer rounds to 90.0. CI fails if Cobertura
  `(lines-covered + branches-covered) / (lines-valid + branches-valid)` is
  below 0.90.
- **PGF-2026-008:** Fair page-fetch rotation persists
  `(last_selected_index + 1) % catalog_size` after attempts (including
  failures). The pipeline stamps via `catalog_with_page_fetch_cursor`. Cursor
  no longer advances only by served work count over the due subset. Backoff
  clocks are unchanged.
- **PGF-2026-009:** Catalog material digest is decoded content plus
  feed-visible fields (title, url, summary*, published_*, order, decoded
  hash). Wire/`raw_sha256` is provenance-only.
- **PGF-2026-010:** Accepted 200 persists raw/decoded hashes, byte counts, and
  `selected_encoding`. 304 preserves those while advancing clocks. Page
  adapter decoding provenance is carried through enrich evidence.
- **PGF-2026-011:** HTTP 304 after a redirect is classified from headers
  actually sent on the final hop. An unconditional final-hop 304 is never
  `NOT_MODIFIED`.
- **PGF-2026-012:** Publish gates the downloaded seven-file candidate
  workspace (not a sibling source checkout), emits `product_sha` after
  force-with-lease push, re-checks that tree, and attests subjects with
  provenance context. `verify-product.yml` checks that SHA (artifact or
  explicit ref), not mutable `main` HEAD.
- **PGF-2026-013:** One-run omission of 1-4 index items is held via private
  `consecutive_absences` (default 0; omitted from JSON when 0). A second
  consecutive observation hard-deletes. The ≥5 removal-ratio quarantine is
  unchanged. No public tombstones.
- **PGF-2026-017:** Unknown non-identity `Content-Encoding` tokens fail closed
  instead of being treated as identity. gzip / deflate / br decode in reverse
  application order; missing-brotli error is unchanged.
- **PGF-2026-022:** Summary extraction skips translation menus, YC/book
  promos, domain-search chrome, and high-link-density related links; quality
  source/score/flags flow from enrich into the catalog. Below-threshold
  scrapes keep prior-good only when it passes the semantic gate, else the
  title blurb. The committed seven-file product is rematerialized offline:
  seven chrome summaries become title blurbs with source `title`;
  `last_generation_id` is stamped; on-disk catalog is schema 3; `check`
  applies the semantic gate to enriched feeds.

### Accepted risk

- **PGF-2026-015:** GitHub raw serves `feeds/*` as `text/plain` (body is still
  RSS / Atom / JSON Feed). Typed CDN hosting is out of scope; documented in
  README and DOCS.md.
- **PGF-2026-016:** Path-aware GitHub ruleset (require PR + CI on source;
  product-path exclude so Update feeds can push `feeds/` + `catalog.json`)
  is maintainer-apply. Operator steps are in DOCS.md; this change does not
  execute `gh api`.
- **PGF-2026-018:** Public replace is still one file at a time (local
  seven-file visibility). A crash can tear the bundle; `check` is the
  detector. No second public tree or directory rename-swap.

## [0.2.0] - 2026-08-28

### Fixed

- **PGF-AUD-001:** Skip-enrich still takes the writer lock, recovers, and
  deep-verifies the existing seven-file bundle before `unchanged`.
  [`tests/characterization/audit/test_aud_001_noop_lock.py`](tests/characterization/audit/test_aud_001_noop_lock.py)
- **PGF-AUD-002:** Writer lock is OS `flock` / `WriteLock`; live locks are never
  stolen by mtime.
  [`tests/characterization/audit/test_aud_002_lock.py`](tests/characterization/audit/test_aud_002_lock.py)
- **PGF-AUD-003:** Independent RSS / Atom / JSON Feed 1.1 contracts (structure,
  identity, clocks, self links).
  [`tests/characterization/audit/test_aud_003_feed_contract.py`](tests/characterization/audit/test_aud_003_feed_contract.py)
- **PGF-AUD-004:** Staging `MANIFEST.json` is an exact contained seven-artifact
  set (no path escape).
  [`tests/characterization/audit/test_aud_004_manifest.py`](tests/characterization/audit/test_aud_004_manifest.py)
- **PGF-AUD-005:** Recover is fail-closed: quarantine on malformed/unverifiable
  pointers; never silently delete.
  [`tests/characterization/audit/test_aud_005_recover.py`](tests/characterization/audit/test_aud_005_recover.py)
- **PGF-AUD-006:** `public_base_url` rejects query / fragment / userinfo; canonical
  directory self links.
  [`tests/characterization/audit/test_aud_006_public_base_url.py`](tests/characterization/audit/test_aud_006_public_base_url.py)
- **PGF-AUD-007:** `raw_sha256` / `bytes_received` are wire bytes, not
  content-decoded. Supersedes the [0.1.0] RV-R-004 caveat.
  [`tests/characterization/audit/test_aud_007_raw_bytes.py`](tests/characterization/audit/test_aud_007_raw_bytes.py)
- **PGF-AUD-008:** Hop policy rejects userinfo, fragments, non-443 HTTPS ports,
  and encoded hosts.
  [`tests/characterization/audit/test_aud_008_redirect_hops.py`](tests/characterization/audit/test_aud_008_redirect_hops.py)
- **PGF-AUD-009:** GC staged generations (keep ≤2; never delete the pointed gen).
  [`tests/characterization/audit/test_aud_009_gc.py`](tests/characterization/audit/test_aud_009_gc.py)
- **PGF-AUD-010:** Dedicated link probes rotate via `catalog.versions`
  `link_validation_cursor`. Supersedes the [0.1.0] H-13 / H-16 / M-04 caveat
  that probes were a capped prefix only.
  [`tests/characterization/audit/test_aud_010_link_cursor.py`](tests/characterization/audit/test_aud_010_link_cursor.py)
- **PGF-AUD-011:** `.github/workflows/release.yml` least privilege (`contents: read`
  on quality/build; `contents: write` only on the release job). No
  characterization test — workflow change.
- **PGF-AUD-012:** `--from-feeds` CLI help + README/DOCS: seed in-memory catalog
  candidate from existing feeds; persist only after successful
  verification/publication.
- **PGF-AUD-016:** HTTP 304 is `NOT_MODIFIED` only with conditionals and prior
  material.
  [`tests/characterization/audit/test_aud_016_304.py`](tests/characterization/audit/test_aud_016_304.py)
- **PGF-AUD-017:** Shared `HostCooldown` injected into enrich and live probes
  (default `0.05`; see Changed).
  [`tests/characterization/audit/test_r005_host_cooldown.py`](tests/characterization/audit/test_r005_host_cooldown.py)
  and `tests/unit/test_settings.py`.
- **PGF-AUD-021:** Catalog churn guard (may land concurrently with this docs pass).
  [`tests/characterization/audit/test_aud_021_catalog_churn.py`](tests/characterization/audit/test_aud_021_catalog_churn.py)
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

- `host_cooldown_seconds` default is `0.05` (was `0`). Set
  `PG_ESSAY_FEEDS_HOST_COOLDOWN_SECONDS=0` to disable.
- Catalog schema 3 is pending if the catalog lane lands; on-disk `catalog.json`
  remains `schema_version: 2` until the next successful update writes a newer
  schema.
- CLI `--abandon-recovery` / `--no-abandon-recovery` (default off): explicit
  repair for irrecoverable `.cache/materialize.json` (quarantines pointer +
  generation). Commands remain `update` + `check` only.
- Consumer pins: README `uvx` and Colab `notebook.ipynb` use
  `@v0.2.0`. `@main` tracks latest (mutable). The `v0.2.0` git tag is not cut
  in this change (**PGF-AUD-020**).
- **PGF-AUD-023:** Changelog claims for this pass link characterization tests
  (or say “workflow / docs” when there is no test).

### Accepted risk

- **PGF-AUD-018:** GitHub raw serves `feeds/*` as `text/plain` (body is still
  RSS / Atom / JSON). Documented in README; not a GitHub Pages / `site/` host.
  Strict readers that require `application/rss+xml` should use local `feeds/`
  from the CLI or Colab.

### Notes

- **PGF-AUD-019:** `verify-product.yml` (`workflow_run` after “Update feeds”) plus
  publish-job Actions artifact attestations bind a check/attestation to the
  product SHA. Partial: the path-aware branch ruleset is maintainer-apply (see
  DOCS.md Branch protection); this change does not execute `gh api`.

## [0.1.0] - 2026-08-21

### Fixed

- **RV-R-001:** Publication recover runs only under the writer lock (no pre-lock
  `recover_materialize` race window).
- **RV-R-002:** Atom feed ids selected via `FeedSnapshot.variant`, not a
  `"simple" in feed_url` substring heuristic.
- **RV-R-003 / RV-R-006:** Staged `MANIFEST.json` digests re-verified before
  public materialize; materialize pointer writes use atomic text writes.
- **RV-R-004:** Page and index `raw_sha256` / `decoded_sha256` fields exist;
  enrich evidence can carry both. The pipeline does not invent `raw_sha256`
  from decoded bytes. Transport may still hash decoded bytes as raw until
  AUD-007 lands.
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
  are wired; the page-fetch cursor rotates (`page_fetch_cursor` in
  `catalog.versions`). Dedicated link probes remain a capped prefix, not a
  rotating cursor. Workflow sets daily caps.
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

[Unreleased]: https://github.com/wyattowalsh/paul-graham-essay-feeds/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/wyattowalsh/paul-graham-essay-feeds/compare/v0.2.0...v1.0.0
[0.2.0]: https://github.com/wyattowalsh/paul-graham-essay-feeds/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/wyattowalsh/paul-graham-essay-feeds/releases/tag/v0.1.0
