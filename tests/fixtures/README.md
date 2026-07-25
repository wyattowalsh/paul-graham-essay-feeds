# Upstream fixtures

Sanitized HTML and encoding samples for discovery, decoding, and summary quality tests.

## Rules

- **No full essay bodies.** Store only short index snippets, marker/layout fragments, and minimal page shells.
- Record provenance in this README or adjacent `*.meta.json` files.
- Prefer synthetic HTML that reproduces structural edge cases over live captures.

## Planned corpus (W0-07)

| ID | Case |
| :--- | :--- |
| `index-marker-basic.html` | Marker gif + anchor association |
| `index-marker-leak.html` | Marker pending across unrelated markup |
| `index-sparse-fallback.html` | Sparse markers force fail-open path |
| `index-duplicate-anchors.html` | Duplicate title/url rows |
| `page-promo-chrome.html` | Nav/footer/promo contamination |
| `page-meta-description.html` | Clean og/meta description preferred |
| `encoding-windows-1252.bin` + expected | Legacy bytes without U+FFFD under policy |
| `encoding-utf8-bom.html` | BOM detection |

Fixtures are added alongside characterization and unit tests as Wave 0–1 land.
