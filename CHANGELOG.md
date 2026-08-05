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

### Changed

- Pipeline machine side-channel action vocabulary is now
  `unchanged | state_changed | updated` (see `PipelineAction` and DOCS CLI
  contract). External consumers that only handled `updated|unchanged` should
  treat `state_changed` like a publishable catalog update.
