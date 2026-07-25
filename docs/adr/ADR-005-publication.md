# ADR-005: Publication and hosting architecture

**Status:** Accepted  
**Date:** 2026-07-25  
**Fixes:** F-008, F-009, F-013, F-033

## Decision

### Generation layout

```text
state/generations/<generation-id>/
  catalog.json
  feeds/{rss.xml,atom.xml,feed.json}
  reports/{change.json,quality.json}
  manifest.json
state/current.json
```

- Generation id is deterministic/content-addressed from logical inputs.
- Generation immutable after validation.
- `current.json` holds generation id, schema version, logical_updated_at, catalog hash, manifest hash, compatibility state.

### Publish order

1. Load/migrate prior canonical state  
2. Build candidate catalog + change set  
3. Build feed snapshot  
4. Render canonical bytes  
5. **Verify entirely in memory**  
6. Stage generation on same filesystem  
7. Set modes (normally `0644` subject to umask), fsync files + dirs  
8. Rename staged dir → immutable generation path  
9. Atomically replace `current.json`  
10. fsync pointer parent  
11. Verify current pointer + generation  
12. Optionally materialize compatibility projections under `feeds/`

Failure before pointer replace changes **no** canonical current state.

### Hosting

- Build fresh `site/` from validated current generation (no symlink reliance for Pages).
- Correct MIME types; self/feed URLs match deploy origin when public base URL configured.
- Pages deploy is the hosted atomic boundary.

### Compatibility

During migration, `feeds/*` may remain as projections. They are never SSOT. Remove feed-embedded operational state after the compatibility window (W5-06).
