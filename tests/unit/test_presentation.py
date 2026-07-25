"""Unit tests for presentation.ProgressReporter."""

from __future__ import annotations

from paul_graham_essay_feeds.presentation import NULL_REPORTER, OutputPolicy, ProgressReporter


def test_null_reporter_returns_same_iterable() -> None:
    data = [1, 2, 3]
    out = NULL_REPORTER.track(data, desc="x")
    assert list(out) == data


def test_quiet_policy_disables_progress() -> None:
    policy = OutputPolicy(quiet=True)
    assert policy.show_progress is False
    rep = ProgressReporter(policy)
    assert list(rep.track([1], desc="x")) == [1]


def test_machine_policy_disables_progress() -> None:
    policy = OutputPolicy(machine=True)
    assert policy.show_progress is False


def test_spinner_quiet_is_noop() -> None:
    with NULL_REPORTER.spinner("x"):
        pass


def test_verbose_policy_shows_progress() -> None:
    policy = OutputPolicy(quiet=False, machine=False)
    assert policy.show_progress is True


def test_track_with_progress_enabled_consumes() -> None:
    rep = ProgressReporter(OutputPolicy(quiet=False, machine=False))
    assert list(rep.track([1, 2], desc="d", unit="u", total=2)) == [1, 2]


def test_spinner_with_progress_enabled() -> None:
    rep = ProgressReporter(OutputPolicy(quiet=False, machine=False))
    with rep.spinner("working"):
        pass
