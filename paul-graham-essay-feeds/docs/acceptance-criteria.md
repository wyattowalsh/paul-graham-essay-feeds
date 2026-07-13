# Acceptance criteria

## Source and canonical data

- [ ] The supplied fixture extracts exactly 233 content items.
- [ ] First item is `How to Earn a Billion Dollars` at
      `https://paulgraham.com/earn.html`.
- [ ] Last item is `This Year We Can End the Death Penalty in California` at
      `https://paulgraham.com/prop62.html`.
- [ ] All positions are contiguous and all titles, URLs, and stable IDs are
      unique.
- [ ] The two Turbify links are direct, HTTPS, and not double-prefixed.
- [ ] Their stable IDs do not change when cache-busting queries change.

## Reconciliation

- [ ] A newest-prefix addition succeeds without override.
- [ ] A removal fails without `--allow-removals`.
- [ ] Retained-item reordering fails without explicit review override.
- [ ] A non-prefix historical insertion fails without
      `--allow-nonprefix-additions`.
- [ ] Title or URL changes are reported explicitly.

## Format validity

- [ ] RSS is well-formed and conforms to the project's RSS 2.0 profile.
- [ ] Atom has all RFC 4287 required feed and entry elements.
- [ ] JSON Feed declares version 1.1 and every item has ID and content.
- [ ] OPML has required root, head, body, and outline attributes.
- [ ] Self/feed URLs are present only when real deployment URLs are configured.

## Cross-format parity

For RSS, Atom, and JSON Feed:

- [ ] Item counts are identical.
- [ ] Item order is identical.
- [ ] Titles are identical.
- [ ] Canonical URLs are identical.
- [ ] Stable IDs are identical.

## Determinism and safety

- [ ] Two identical builds produce byte-identical files.
- [ ] A no-op update preserves output modification times.
- [ ] A failed fetch, extraction, reconciliation, or validation writes nothing.
- [ ] Atomic publication cannot leave mixed-generation feed files.
- [ ] Conditional HTTP 304 preserves the current artifact set.
- [ ] Unit tests require no public network.

## CLI and automation

- [ ] `update`, `build`, `check`, and `diff` have help text and stable exit codes.
- [ ] `check` performs no network access.
- [ ] `diff` writes no artifacts.
- [ ] CI runs format, lint, type, tests, build, and check.
- [ ] Scheduled updates create a reviewable change instead of bypassing failed
      validation.

## Documentation

- [ ] README lists every subscription URL and content type after deployment.
- [ ] Observation timestamps are clearly distinguished from publication dates.
- [ ] The unofficial-project disclaimer is prominent.
- [ ] License status is explicit before public release.
