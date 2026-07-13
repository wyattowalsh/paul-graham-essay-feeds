# Changelog

## Unreleased

### Changed

- **Denested layout:** package lives at the repository root (no nested
  `paul-graham-essay-feeds/paul-graham-essay-feeds/`). Workflows and Vercel Root
  Directory use the repo root.
- Removed planning kickoff cruft (`CODEX_KICKOFF_PROMPT`, `START_CODEX.*`,
  `planning-manifest.json`, `bundle-validation.json`, `scripts/verify_bundle.py`).

### Fixed / hardened (review RV-010, RV-016–RV-018)

- Automation PR step branches from current HEAD (preserves update artifacts) and
  uses force-with-lease expect from `origin/automation/feed-update` only (RV-016).
- Ops set (`state.json`, validation report, `SHA256SUMS`) published as a second
  staged transaction under the pipeline lock (RV-017).
- Remaining Pages-primary wording superseded in ROADMAP, implementation-plan,
  and CODEX kickoff (RV-018).
- Ship surface still requires an explicit maintainer commit for RV-010.

### Fixed / hardened (review RV-009–RV-015)

- Unified dirty-subset publish under `repo_root/.publish-staging-*` with relative-path
  MANIFEST v2 so `feeds/` + `data/essays.json` share one transaction (no multi-parent desync).
- Source URL scheme gate: HTTPS required; HTTP only for loopback test hosts.
- Deploy workflow comments describe Mode A/B honestly (not pretends prebuilt).
- Automation branch push uses explicit `--force-with-lease=ref:expect` after fetch.
- Makefile fixture-build public base aligned to Vercel example URL.
- Documented Atom `feed_id` stability (D-009); default tag URI unchanged.
- Root `.gitignore` + package ignore for `.publish-staging-*`.

### Fixed / hardened (review RV-001–RV-008)

- GitHub Actions at **git root** with `working-directory: paul-graham-essay-feeds`
  (nested package workflows removed).
- Two-phase staged multi-artifact publish with MANIFEST recovery (`io.py`).
- Source final-host allowlist after redirects (`source_allowed_hosts`).
- Third-party Actions pinned to full commit SHAs.
- No-op update fails when feed artifacts are missing.
- Update/build hold the lock for the full critical section (`already_locked` for nested check).
- Scheduled update workflow runs explicit `check` and accurate PR body.
- Co-publish feeds + `data/essays.json` as one generation set.

### Changed

- Primary hosting target is **Vercel** (`vercel.json`, `scripts/assemble_public.sh`);
  GitHub Pages is no longer the primary deploy path.
- Default public base URL example:
  `https://paul-graham-essay-feeds.vercel.app/`.

## 0.1.0 — 2026-07-12

First production-quality multi-format release.

### Added

- Google Colab notebook `notebooks/regenerate_feeds.ipynb` to clone, install
  (uv + Python 3.13), regenerate, validate, and download feed artifacts.
- Typed Python 3.13 package `paul_graham_essay_feeds` with zero runtime dependencies.
- Canonical essay model and schema-versioned `data/essays.json` / `data/state.json`.
- Safe updater: stdlib conditional HTTP fetch, marker extraction, fail-closed reconciliation.
- Pure renderers for RSS 2.0, Atom 1.0, JSON Feed 1.1, and OPML 2.0.
- CLI `pg-essay-feeds` with `update`, `build`, `check`, and `diff`.
- Local structural validation, cross-format parity, atomic multi-artifact publish.
- Pytest suite (offline unit/integration local HTTP server).
- Static subscription index under `site/`.

### Notes

- Baseline import timestamp `2026-07-11T07:24:19+00:00` seeds historical observation times.
- Minimum safety floor starts at 233 items and is not a permanent exact count.
