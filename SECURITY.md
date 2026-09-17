# Security Policy

## Reporting a vulnerability

Report security issues **privately** via GitHub Security Advisories:

https://github.com/wyattowalsh/paul-graham-essay-feeds/security/advisories/new

Do not open a public issue for a vulnerability.

Include the affected version (`paul_graham_essay_feeds.__version__` or git
SHA), impact, and a reproducer if you have one.

## Supported versions

The current tagged release is **1.0.1** (`v1.0.1`). Do not treat a GitHub
Release as a substitute for an annotated tag. Do not move or overwrite
`v1.0.0`. `protect-version-tags` (id `22371020`) no longer restricts tag
**creation**; `update` / `deletion` / `non_fast_forward` remain, with an empty
bypass list (`current_user_can_bypass: never`).

## Scope

In scope: the CLI, publication lock/staging, HTTP client, and the committed
`catalog.json` / `feeds/` product.

Out of scope: paulgraham.com, GitHub raw MIME (`text/plain`), GitHub Pages
generic `application/xml` / `application/json` types, and third-party feed
readers.

## Verifying a GitHub Release

Release assets include wheel, sdist, frozen `requirements.txt`, CycloneDX 1.5
`bom.cdx.json` (default runtime graph; no `brotli` extra, no dev tools), and
`SHA256SUMS.txt` covering those four classes of file. Attestations are issued
for the same subjects. Attested `requirements.txt` is a name==version
inventory; **`uv.lock` is the install-integrity SSOT** (`uv sync --locked`).

```bash
sha256sum -c SHA256SUMS.txt
gh attestation verify dist/*.whl --repo wyattowalsh/paul-graham-essay-feeds
gh attestation verify dist/*.tar.gz --repo wyattowalsh/paul-graham-essay-feeds
gh attestation verify dist/requirements.txt --repo wyattowalsh/paul-graham-essay-feeds
gh attestation verify dist/bom.cdx.json --repo wyattowalsh/paul-graham-essay-feeds
gh attestation verify dist/SHA256SUMS.txt --repo wyattowalsh/paul-graham-essay-feeds
```

Expect repository `wyattowalsh/paul-graham-essay-feeds` and workflow
`.github/workflows/release.yml`. Generation of attestations is not the
security benefit; verification is.
