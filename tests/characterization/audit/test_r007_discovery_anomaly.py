"""RV-R-007: discovery anomaly uses true stable-id set overlap."""

from __future__ import annotations

from paul_graham_essay_feeds.discover import (
    ExtractionReport,
    ExtractionStrategy,
    evaluate_discovery_anomaly,
)


def _report(*, fallback: bool = False) -> ExtractionReport:
    return ExtractionReport(
        strategy=ExtractionStrategy.MARKER,
        fallback_used=fallback,
        marked_count=20,
        drift_score=0.0,
    )


def test_same_size_disjoint_ids_quarantine() -> None:
    prior = {f"https://paulgraham.com/old{i}.html" for i in range(25)}
    discovered = {f"https://paulgraham.com/new{i}.html" for i in range(25)}
    reason = evaluate_discovery_anomaly(prior, discovered, report=_report())
    assert reason is not None
    assert "overlap" in reason or "removal" in reason


def test_small_removal_under_threshold_ok() -> None:
    prior = {f"https://paulgraham.com/e{i}.html" for i in range(20)}
    discovered = set(list(prior)[:19])  # remove 1 of 20 = 5% < 15%
    reason = evaluate_discovery_anomaly(prior, discovered, report=_report())
    assert reason is None


def test_empty_prior_never_quarantines() -> None:
    reason = evaluate_discovery_anomaly(set(), {"https://paulgraham.com/a.html"}, report=_report())
    assert reason is None
