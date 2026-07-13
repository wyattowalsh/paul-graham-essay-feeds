# Implementation plan

## Phase 0: protect the baseline

- [ ] Run the preserved baseline tests and verifier.
- [ ] Copy the baseline item manifest into the new canonical import path.
- [ ] Add regression fixtures for all 233 titles, URLs, IDs, and ordering.
- [ ] Record the historical import timestamp without calling it publication time.

## Phase 1: establish the package and canonical model

- [ ] Convert `pyproject.toml` to a package using the `src/` layout.
- [ ] Add the `pg-essay-feeds` console script.
- [ ] Implement typed models, config loading, deterministic JSON, and schema
      versions.
- [ ] Implement import/migration from the reference manifest.

## Phase 2: port the safe update pipeline

- [ ] Port fetch limits, retries, conditional headers, and local fixture support.
- [ ] Port marker extraction and controlled fallback.
- [ ] Port canonical URL and text normalization.
- [ ] Port diff/reconciliation policy and explicit overrides.
- [ ] Implement lock, staging, atomic multi-file publication, and backups.

## Phase 3: implement renderers

- [ ] RSS 2.0 renderer matching the reference behavior.
- [ ] Atom 1.0 renderer with stable entry update semantics.
- [ ] JSON Feed 1.1 renderer with metadata-only `content_text`.
- [ ] OPML 2.0 subscription catalog requiring deployment URLs.

## Phase 4: validation and parity

- [ ] Strict local validation for each format.
- [ ] Cross-format parity checks for count, order, title, URL, and ID.
- [ ] No-op hash and mtime checks.
- [ ] Machine-readable validation report and checksums.

## Phase 5: CLI and operator UX

- [ ] `update`, `build`, `check`, and `diff` commands.
- [ ] Clear exit codes and concise human-readable summaries.
- [ ] Shell and PowerShell wrappers only if they materially simplify operation.
- [ ] Config example and environment-variable documentation.

## Phase 6: CI, automation, and hosting

- [ ] CI quality gates and fixture build.
- [ ] Scheduled/manual update workflow that opens or refreshes an automation PR.
- [ ] Vercel static deployment for assembled `public/` (site + feeds).
- [ ] Least-privilege workflow permissions and pinned external actions.

## Phase 7: documentation and release

- [ ] Replace planning README with user documentation.
- [ ] Add architecture and schema references generated from implementation.
- [ ] Add compatibility and deployment checks.
- [ ] Resolve the code license before public release.
- [ ] Tag `0.1.0` only after all acceptance criteria pass.
