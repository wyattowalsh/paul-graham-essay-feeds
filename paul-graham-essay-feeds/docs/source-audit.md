# Source and baseline audit

## Source

Official index:

```text
https://paulgraham.com/articles.html
```

Verified on 2026-07-11. The public page begins with `How to Earn a Billion
Dollars` and the supplied source snapshot aligns with the audited baseline.

## Baseline

The preserved RSS implementation recorded:

- 238 total anchors encountered;
- 233 essay-row-marker anchors;
- 233 unique feed items;
- first item: `How to Earn a Billion Dollars`;
- last item: `This Year We Can End the Death Penalty in California`;
- two direct Turbify text resources for ANSI Common Lisp chapters;
- no duplicate item URLs or GUIDs;
- exact source ordering.

## Legacy RSS mismatch addressed by the baseline

The older scraped RSS omitted fourteen newer essays and malformed both absolute
Turbify chapter URLs by prefixing them with the Paul Graham domain. The preserved
baseline corrects both defects.

## Snapshot caveat

`fixtures/articles-2026-07-11.fragment.html` is the relevant page fragment
captured during planning. It is sufficient for extraction regression testing but
is not represented as a complete archival copy of the website.
