"""Centralized progress and console output policy (ADR-006)."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TypeVar

from tqdm import tqdm

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class OutputPolicy:
    """How a command should present progress and diagnostics."""

    quiet: bool = False
    verbose: bool = False
    machine: bool = False  # JSON / non-TTY: no progress bars

    @property
    def show_progress(self) -> bool:
        return not self.quiet and not self.machine


class ProgressReporter:
    """Thread-safe-enough progress facade; quiet/machine emit nothing."""

    def __init__(self, policy: OutputPolicy | None = None) -> None:
        self.policy = policy or OutputPolicy()

    def track(
        self,
        iterable: Iterable[T],
        *,
        desc: str = "",
        unit: str = "it",
        total: int | None = None,
    ) -> Iterable[T]:
        if not self.policy.show_progress:
            return iterable
        return tqdm(iterable, desc=desc, unit=unit, total=total)

    @contextmanager
    def spinner(self, desc: str = "") -> Iterator[None]:
        if not self.policy.show_progress:
            yield
            return
        bar = tqdm(total=0, desc=desc, bar_format="{desc}")
        try:
            yield
        finally:
            bar.close()


NULL_REPORTER = ProgressReporter(OutputPolicy(quiet=True))
