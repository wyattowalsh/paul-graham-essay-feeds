# Product requirements

## Problem

Paul Graham's official essay index is authoritative and well ordered, but its
legacy scraped RSS is incomplete and malformed at two external URLs. Readers and
automations benefit from modern, validated syndication formats generated from a
single source of truth.

## Primary users

- Feed-reader users who want a complete Paul Graham essay subscription.
- Developers who prefer Atom or JSON Feed over RSS.
- Maintainers who need deterministic updates, auditability, and safe failure
  modes.
- The repository owner, who wants a low-maintenance public utility.

## Goals

1. Represent every official essay-index content item exactly once and in source
   order.
2. Publish RSS 2.0, Atom 1.0, and JSON Feed 1.1 with cross-format parity.
3. Publish an OPML 2.0 catalog that makes the generated feeds easy to import.
4. Update safely and automatically without silently accepting destructive source
   changes.
5. Remain metadata-only and link back to the official source.
6. Keep local operation easy: one update command and one offline check command.

## Non-goals

- Republishing complete essays.
- Inferring original publication dates from page content, archive snapshots, or
  HTTP headers.
- Supporting historical RSS dialects.
- Becoming an official Paul Graham service.
- Covering talks, interviews, books, or social posts.

## Functional requirements

- Fetch the official index with conditional HTTP support and retries.
- Extract the marked essay list, with a controlled fallback.
- Canonicalize URLs and protect the two external chapter resources.
- Persist stable IDs and feed-observation timestamps.
- Reconcile changes against prior canonical state.
- Build, validate, and atomically publish all outputs.
- Expose update, build, check, and diff commands.
- Produce machine-readable reports and checksums.
- Support local source fixtures for deterministic builds.

## Reliability requirements

- A no-op update changes no output bytes or modification times.
- Network, parse, validation, or reconciliation failures leave prior outputs
  intact.
- New newest-first items are accepted automatically.
- Removals, retained-item reorderings, and mid-history insertions fail closed by
  default.
- Every content feed has identical item count, order, title, URL, and stable ID.

## Success metrics

- 100% parity against the audited 233-item baseline.
- All local quality gates and structural validators pass.
- Scheduled updates can run unattended without feed churn.
- Public subscription URLs work from at least one RSS reader, one Atom reader,
  and one JSON Feed client after deployment.
