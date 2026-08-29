"""RV-R-005: HostCooldown enforces injectable inter-request gaps."""

from __future__ import annotations

import random

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


def test_host_cooldown_jitter_adds_to_remaining() -> None:
    now = [0.0]
    sleeps: list[float] = []
    rng = random.Random(0)

    def clock() -> float:
        return now[0]

    def sleeper(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    cd = HostCooldown(1.0, clock=clock, sleeper=sleeper, jitter=0.5, rng=rng)
    cd.wait("paulgraham.com")
    now[0] = 0.4
    cd.wait("paulgraham.com")
    expected_extra = random.Random(0).random() * 0.5
    assert len(sleeps) == 1
    assert abs(sleeps[0] - (0.6 + expected_extra)) < 1e-9
