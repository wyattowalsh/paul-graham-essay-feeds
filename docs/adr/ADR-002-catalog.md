# ADR-002: Catalog schema, lifecycle, migrations

**Status:** Accepted  
**Date:** 2026-07-25  
**Fixes:** F-001, F-002, F-027, F-030, F-031, F-034

## Decision

Use a **schema-versioned JSON catalog** as the durable source of truth (not `data/essays.json` flat legacy, not feed-embedded skip meta).

### Schema SSOT

**Pydantic models in `catalog_models.py`** (and related modules) are the contract.
There is no separate `schemas/*.json` tree to keep in sync.

### Models (logical)

- `Catalog`, `CatalogEntry`, `ResourceState`/`FetchState`, `EnrichmentState`
- `DiscoverySnapshot`, `ExtractionReport`, `RefreshPlan`, `RefreshDecision`
- `ChangeSet`, `FeedEntrySnapshot`, `FeedSnapshot`
- `VerificationReport`, `ArtifactManifest`, `PublicationResult`

### Persistence rules

- `extra="forbid"`, aware UTC datetimes, closed enums
- Deterministic JSON key order and trailing newline policy
- Explicit idempotent migrations with old-version fixtures
- No full page bodies; store evidence hashes and short summaries only
- Lifecycle: `active` | `missing_candidate` | `tombstoned`
- Preserve **prior-good** summary/title/url evidence on recoverable failures

### Global fields

Schema version; source + URL policy versions; generator/extractor/decoder/renderer/verifier versions; material config fingerprint; index resource state; ordered entry map; last successful generation ref; migration history.

### Entry fields

Stable id + provenance; title; normalized URL; position; first/last seen; observed_updated_at; published_at + evidence; page resource state; summary + source + quality + last-good; attempt/failure evidence without overwriting success.

## Consequences

Index-only skip is invalid. Refresh planning keys off catalog + validators + policy versions.
