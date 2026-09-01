# Security Policy

## Reporting a vulnerability

Report security issues **privately** via GitHub Security Advisories:

https://github.com/wyattowalsh/paul-graham-essay-feeds/security/advisories/new

Do not open a public issue for a vulnerability.

Include the affected version (`paul_graham_essay_feeds.__version__` or git
SHA), impact, and a reproducer if you have one.

## Supported versions

The intended release is **1.0.0**. Until the `v1.0.0` tag exists, `main` is
the supported line.

## Scope

In scope: the CLI, publication lock/staging, HTTP client, and the committed
`catalog.json` / `feeds/` product.

Out of scope: paulgraham.com, GitHub raw MIME (`text/plain` on hosted
subscribe URLs), and third-party feed readers.

## Verifying a GitHub Release

After `v1.0.0` exists, consumers can check wheel/sdist integrity:

```bash
sha256sum -c SHA256SUMS.txt
gh attestation verify dist/*.whl --repo wyattowalsh/paul-graham-essay-feeds
gh attestation verify dist/*.tar.gz --repo wyattowalsh/paul-graham-essay-feeds
```

Expect repository `wyattowalsh/paul-graham-essay-feeds` and workflow
`.github/workflows/release.yml`. Generation of attestations is not the
security benefit; verification is.
