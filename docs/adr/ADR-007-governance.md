# ADR-007: Generated content and release governance

**Status:** Accepted  
**Date:** 2026-07-25  
**Fixes:** F-051, F-052, F-053

## Content

- MIT license covers **code**, not Paul Graham essays.
- Summaries are short, source-derived, reviewable excerpts with provenance (source kind, policy version, quality).
- No full essay body storage or redistribution.
- Removal/takedown contact documented in SECURITY/README.
- Quality reports must not dump over-policy content.

## Release

- Tag must match package version (`v{version}`).
- Trusted publishing (OIDC) + artifact attestations when configured.
- CHANGELOG policy: user-facing changes only; no floating-main as default install path.
- CODEOWNERS for hotspots: `cli.py`, workflows, catalog schemas, publication.

## Automation evidence

Scheduled PRs attach deterministic change + quality reports (counts, deltas, encodings, failures, hashes).
