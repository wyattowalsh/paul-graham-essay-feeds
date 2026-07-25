# ADR-008: CI zero-error / zero-warning policy

**Status:** Accepted  
**Date:** 2026-07-25  
**Fixes:** F-038, F-046, F-047, F-049

## Decision

Every GitHub Actions workflow must complete with:

1. Exit code 0 on the supported matrix  
2. Zero `::error::` and `::warning::` annotations  
3. actionlint clean  
4. zizmor clean (or zero unjustified findings)  
5. CodeQL without unexplained high/critical  
6. dependency-review policy pass  
7. Full-SHA action pins with version comments  
8. Least privilege: network generation jobs `contents: read` only  
9. Multi-line scripts use `set -euo pipefail`  

## Workflow split

- **CI:** Linux matrix 3.12/3.13/3.14; wheel/sdist inspect; offline suite  
- **update-feeds:** generate (no write token) → validate artifact → publish job (write, no network fetch)  
- **release:** job-level permissions; OIDC publish gated  
- **security:** dependency-review, CodeQL, actionlint, zizmor  
- **pages:** deploy only validated `site/` from current generation  

## Local mirror

`just ci-local` runs the offline subset of these gates.
