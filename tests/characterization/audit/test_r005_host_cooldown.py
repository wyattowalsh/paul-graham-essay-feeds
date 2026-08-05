"""RV-R-005: HostCooldown enforces injectable inter-request gaps."""

from __future__ import annotations

from paul_graham_essay_feeds.http import HostCooldown


def test_host_cooldown_gap_with_fake_clock() -> None:
    now = [0.0]
    sleeps: list[float] = []

    def clock() -> float:
        return now[0]

    def sleeper(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    cd = HostCooldown(1.0, clock=clock, sleeper=sleeper)
    cd.wait("paulgraham.com")
    assert sleeps == []
    now[0] = 0.4
    cd.wait("paulgraham.com")
    assert len(sleeps) == 1
    assert abs(sleeps[0] - 0.6) < 1e-9


def test_host_cooldown_zero_no_sleep() -> None:
    sleeps: list[float] = []
    cd = HostCooldown(0.0, clock=lambda: 0.0, sleeper=lambda s: sleeps.append(s))
    cd.wait("paulgraham.com")
    cd.wait("paulgraham.com")
    assert sleeps == []
