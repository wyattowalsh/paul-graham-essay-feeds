# Upstream fixtures

Sanitized HTML and encoding samples for discovery, decoding, and summary quality tests.

## Rules

- **No full essay bodies.** Store only short index snippets, marker/layout fragments, and minimal page shells.
- Record provenance in this README or adjacent `*.meta.json` files.
- Prefer synthetic HTML that reproduces structural edge cases over live captures.

## Corpus (present)

| ID | Case | Provenance |
| :--- | :--- | :--- |
| `index-marker-basic.html` | Marker gif + anchor association | Synthetic; turbifycdn `the-reddits-2.gif` |
| `index-marker-leak.html` | Marker pending across unrelated markup (anti-leak) | Synthetic table rows |
| `index-sparse-fallback.html` | Sparse/missing markers for fail-closed vs fallback | Synthetic unmarked anchors |
| `index-duplicate-anchors.html` | Duplicate title/url rows (first-wins) | Synthetic |
| `page-promo-chrome.html` | Nav/footer/promo contamination | Synthetic essay shell |
| `page-meta-description.html` | Clean og/meta description preferred | Synthetic essay shell |

PGF-2026-022 seven-essay chrome cases live as minimized HTML in
`tests/characterization/test_pgf_2026_022_extraction.py` (not live captures).
| `encoding-windows-1252.bin` | Legacy bytes (smart quotes) for decode policy | Synthetic windows-1252 |
| `encoding-utf8-bom.html` | UTF-8 BOM detection | Synthetic UTF-8 with BOM |

Index fixtures are sized for lowered `min_items` in unit/characterization tests (not the live `MIN_ITEMS` floor). Encoding fixtures are loaded by decoding / F-020 tests.
