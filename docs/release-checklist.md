# Release checklist

## Repository

- [ ] Create `wyattowalsh/paul-graham-essay-feeds`.
- [ ] Set default branch and branch protection.
- [ ] Add topics: `paul-graham`, `essays`, `rss`, `atom`, `json-feed`, `opml`,
      `syndication`.
- [ ] Resolve and add a code license.
- [ ] Confirm unofficial-project language.

## Implementation

- [ ] All phases in `implementation-plan.md` complete.
- [ ] All acceptance criteria pass.
- [ ] Runtime dependency rationale documented.
- [ ] State schema and migration path documented.
- [ ] Baseline reference remains unchanged.

## Automation

- [ ] CI passes from a clean clone.
- [ ] Scheduled update creates a reviewable change.
- [ ] Failed validation cannot publish or deploy.
- [ ] Workflow permissions are least-privilege.
- [ ] Third-party actions are pinned or removed.

## Deployment

- [ ] Vercel project linked (Root Directory = repository root).
- [ ] Public base URL configured (`deployment.public_base_url` / env / CLI).
- [ ] Optional GHA deploy secrets set if using Mode B (`VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID`).
- [ ] Feed self URLs match deployed URLs.
- [ ] All public outputs parse successfully.
- [ ] OPML imports successfully.
- [ ] Static subscription index is accessible.

## Release

- [ ] README no longer describes the repository as a planning scaffold.
- [ ] `CHANGELOG.md` contains final `0.1.0` notes.
- [ ] Version is `0.1.0` and `uv.lock` is current.
- [ ] Tag and GitHub release created only after deployment validation.
