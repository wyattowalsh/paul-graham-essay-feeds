# Architecture

## Overview

The system is a deterministic pipeline:

```text
official HTML or fixture
        │
        ▼
fetch → extract → normalize → reconcile → canonical state
                                         │
                    ┌────────────────────┼────────────────────┐
                    ▼                    ▼                    ▼
                 RSS 2.0              Atom 1.0          JSON Feed 1.1
                                                             │
                                                             ▼
                                                  OPML subscription catalog
                    └────────────────────┬────────────────────┘
                                         ▼
                              cross-format validation
                                         ▼
                           atomic artifact publication
```

## Package boundaries

- `config.py`: typed configuration resolution from defaults, TOML, environment,
  and CLI overrides.
- `domain.py`: immutable canonical models and schema-versioned serialization.
- `fetch.py`: conditional HTTP, retries, response limits, and fetch metadata.
- `extract.py`: HTML parsing, marker detection, fallback filtering, text and URL
  normalization.
- `reconcile.py`: explicit change policy and diff production.
- `state.py`: canonical item/state persistence and timestamp semantics.
- `renderers/*`: pure deterministic serializers.
- `validation.py`: per-format and cross-format validation.
- `io.py`: atomic write sets, backups, hashes, and lock handling.
- `build.py`: orchestration without CLI concerns.
- `cli.py`: user-facing commands and exit codes.

## Canonical invariants

1. `position` is one-based and contiguous.
2. `title` is Unicode NFC, whitespace-normalized, and XML-safe.
3. `url` is absolute HTTPS and host-allowlisted.
4. `stable_id` is immutable across runs and formats.
5. `first_seen_at` never changes.
6. `last_changed_at` changes only for material item metadata changes.
7. The item sequence is newest to oldest and unique by stable ID and URL.
8. Generated content feeds are exact projections of the same sequence.

## State model

`data/essays.json` is the durable canonical item catalog. `data/state.json`
contains transport and build state such as:

- source ETag and Last-Modified;
- source and logical signatures;
- feed configuration signature;
- last successful check and build timestamps;
- current safety floor;
- schema versions.

The state files are inputs to no-op detection and reconciliation, not incidental
reports.

## Timestamp semantics

The project stores **observation timestamps**, not original publication dates.
The baseline import timestamp initializes historical records. A newly observed
item gets the current observation time. An existing item keeps its timestamp
unless title, canonical URL, or other material canonical metadata changes.

Atom's required `updated` field maps to `last_changed_at`. RSS and JSON Feed omit
item publication dates. This is precise, stable, and honest.

## Atomic publication

Render every candidate artifact into memory, validate the complete set, then
publish via **unified dirty-subset staging** under the package root:

1. Compare intended bytes to live files; skip identical paths (preserve mtimes).
2. Stage all dirty paths into one `repo_root/.publish-staging-*` tree using
   **relative path keys** (so `feeds/*` and `data/essays.json` share one
   MANIFEST).
3. Write MANIFEST v2 with `complete: true`, backup live targets, then
   `os.replace` each staged file.
4. Incomplete staging never touches live files; crash mid-replace is finished by
   `recover_pending_publish`.

Ops files (`state.json`, validation report, `SHA256SUMS`) are published in a
**second** dirty-subset staging transaction under the same pipeline lock after
generation publish succeeds—so ops files stay consistent with each other. A crash
between the two phases can leave feeds new and ops briefly stale (acceptable;
re-run `check` / `update`). Keep bounded backups of the last known-good set.

## Extensibility

A future renderer implements the same small protocol:

```python
class Renderer(Protocol):
    def render(self, context: BuildContext) -> bytes: ...
```

New formats must not modify extraction, reconciliation, or canonical state.
