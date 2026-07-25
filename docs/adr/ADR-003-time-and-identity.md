# ADR-003: Timestamp and identity semantics

**Status:** Accepted  
**Date:** 2026-07-25  
**Fixes:** F-004, F-005, F-019, F-032

## Timestamps

| Field | Meaning | Public feed use |
| :--- | :--- | :--- |
| `first_seen_at` | First successful official-index observation | Not a publication date |
| `last_seen_at` | Latest successful index observation containing entry | Catalog only |
| `last_checked_at` | Latest request/check attempt | Never content timestamp |
| `observed_updated_at` | Latest material metadata/page-content change | Atom entry `updated`; JSON `date_modified` |
| `published_at` | Exact trustworthy source date only | RSS `pubDate`, Atom `published`, JSON `date_published` |
| `logical_updated_at` | Latest material change in a generation | Atom feed `updated`; RSS `lastBuildDate` |

Rules:

- Reject naive datetimes; store UTC only.
- Month-year text remains `published_hint` only.
- 304 / unchanged body updates check evidence only.
- Never treat 1970-01-01 as observation history.
- Bootstrap: prefer earliest defensible git observation; else labeled migration observation with provenance.

## Identity

- Stable id from normalized permalink URL for PG essays; UUID URN for protected Turbify ACL chapters.
- Normalize `www.paulgraham.com` → `paulgraham.com`; strip fragments; absolute HTTPS allowlist (loopback HTTP test-only).
- Canonical links are **hints**, not automatic identity rewrites.
- Collection invariants: unique stable ids, unique material URLs where required, valid positions, protected chapters present.
