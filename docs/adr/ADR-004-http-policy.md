# ADR-004: HTTP transport, cache, encoding, retry, politeness

**Status:** Accepted  
**Date:** 2026-07-25  
**Fixes:** F-016, F-020, F-021, F-022, F-023, F-024, F-028, F-029, F-036

## Transport

- Preserve hop-by-hop validation, host allowlist, HTTPS policy, loopback test support, `trust_env=False`.
- Typed `FetchEvidence`: requested/final URL, redirect chain, method, status, result kind (`fetched` | `not_modified` | `skipped` | `blocked` | `failed`), media type, charset, ETag, Last-Modified, Retry-After, raw/decoded hashes, selected encoding, error class.
- HEAD: do **not** treat representation `Content-Length` as downloaded-body budget.
- GET: hard-cap transferred body bytes; validate media type before HTML parse.
- Local files: same capped reader (no full read before size check).

## Encoding

Deterministic HTML policy:

1. BOM  
2. Valid transport charset  
3. Bounded in-document meta prescan  
4. Strict UTF-8  
5. Documented Windows-1252 fallback (not ISO-8859-1 alone)  
6. Reject/quarantine unexpected U+FFFD and disallowed controls  

Record selected encoding + evidence. Network and local share one decoder.

## Retry

- Retry only idempotent classified transients.
- Honor bounded `Retry-After` (delta-seconds + HTTP-date) for 429/503.
- Else bounded full jitter (`wait_random_exponential` semantics); name strategy accurately.
- Granular connect/read/write/pool timeouts.

## Cache and politeness

- Persist ETag/Last-Modified/raw/decoded hashes per resource; conditional GET; model 304.
- Per-host rate policy in addition to worker concurrency.
- Cache robots policy under documented UA; failure behavior explicit.
- Reuse successful page GET evidence for link health.
- Shared request budget across scheduled modes.
- User-Agent always retains repository contact (including link-check role suffix).
