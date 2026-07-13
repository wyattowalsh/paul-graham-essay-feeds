# Security and operations

## Threat model

The updater processes remote HTML and writes public artifacts. Relevant risks
include partial responses, hostile or unexpected links, parser regressions,
resource exhaustion, concurrent runs, corrupted state, workflow-token abuse,
and silent destructive source changes.

## Fetch controls

- HTTPS source URL and **redirect host validation** (final URL host must be in
  `source.source_allowed_hosts`, default `paulgraham.com`).
- Separate **item** host allowlist (`allowed_hosts`) for essay links.
- 5 MiB response limit.
- Explicit accepted content types.
- Bounded connect/read timeout.
- Bounded exponential retry for transient status codes.
- Identity encoding unless decompression is explicitly bounded.
- Conditional ETag and Last-Modified requests.
- Transport: stdlib `urllib.request` only (no httpx/requests/trafilatura).

## Extraction controls

- Use the site's known essay-row marker as the primary signal.
- Keep fallback filtering narrow and observable.
- Reject unexpected visible external hosts or link formats.
- Normalize Unicode and remove forbidden XML control characters.
- Reject credentials, fragments, unsupported schemes, and malformed double
  prefixes in URLs.

## Change controls

- Maintain a minimum count safety floor.
- Accept only newest-prefix additions automatically.
- Fail closed on removals, retained-item reorderings, and non-prefix insertions.
- Produce a structured change report before writes.
- Require explicit reviewed overrides for exceptional source edits.

## Write controls

- Acquire a repository-local update lock for the **full** update/build critical
  section (fetch through publish).
- Stage multi-file generation sets (feeds + essays.json) before any live
  `os.replace`; MANIFEST recovery for interrupted swaps.
- Validate the complete candidate set before replacing live artifacts.
- Preserve bounded backups of the last known-good artifact set.
- Do not rewrite unchanged feed files (no-op byte + mtime stability).
- Logical no-op with **missing** feed files is a hard failure.

## CI and GitHub Actions

- Workflows live at the **git root** with
  `working-directory: paul-graham-essay-feeds`.
- Use least-privilege `permissions` blocks.
- Avoid untrusted pull-request code with write tokens.
- Pin third-party actions to **full commit SHAs**.
- Keep scheduled update and Vercel deploy jobs separate.
- Never persist secrets in generated reports or artifacts.
- Require validation before commit, PR creation, or deployment.

## Observability

Every successful or failed operation should produce concise logs. Successful
updates also write a machine-readable report containing source metadata,
changes, counts, signatures, output hashes, and validation results. Reports must
not contain source bodies or credentials.
