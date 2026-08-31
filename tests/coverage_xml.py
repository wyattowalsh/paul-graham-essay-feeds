"""Cobertura coverage.xml raw-ratio gate (PGF-2026-007).

coverage.py ``fail_under`` compares a *rounded* percentage at
``[tool.coverage.report] precision``. With ``precision = 1``, 89.955% becomes
90.0 and incorrectly passes a 90 floor. This helper uses the XML totals
``(lines-covered + branches-covered) / (lines-valid + branches-valid)`` with no
rounding.
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

COVERAGE_FLOOR = 0.90


@dataclass(frozen=True, slots=True)
class CoverageTotals:
    """Line + branch counts from a Cobertura ``<coverage>`` root."""

    lines_covered: int
    lines_valid: int
    branches_covered: int
    branches_valid: int

    @property
    def covered(self) -> int:
        return self.lines_covered + self.branches_covered

    @property
    def valid(self) -> int:
        return self.lines_valid + self.branches_valid

    @property
    def ratio(self) -> float:
        if self.valid <= 0:
            return 0.0
        return self.covered / self.valid


def _attr_int(root: ET.Element, name: str, *, default: int | None = None) -> int:
    raw = root.get(name)
    if raw is None or raw == "":
        if default is not None:
            return default
        raise ValueError(f"coverage.xml missing {name}")
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"coverage.xml {name}={raw!r} is not an int") from exc
    if value < 0:
        raise ValueError(f"coverage.xml {name}={value} is negative")
    return value


def parse_cobertura_totals(path: Path) -> CoverageTotals:
    """Read line+branch totals from a coverage.py Cobertura XML report."""

    root = ET.parse(path).getroot()
    tag = root.tag.rsplit("}", 1)[-1]
    if tag != "coverage":
        raise ValueError(f"coverage.xml root is {root.tag!r}, expected coverage")
    return CoverageTotals(
        lines_covered=_attr_int(root, "lines-covered"),
        lines_valid=_attr_int(root, "lines-valid"),
        branches_covered=_attr_int(root, "branches-covered", default=0),
        branches_valid=_attr_int(root, "branches-valid", default=0),
    )


def ratio_meets_floor(covered: int, valid: int, floor: float = COVERAGE_FLOOR) -> bool:
    """True iff ``covered/valid`` is at least ``floor`` (raw division, no round)."""

    if valid <= 0 or covered < 0:
        return False
    return (covered / valid) >= floor


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail if coverage.xml covered/valid (lines+branches) is below 0.90"
    )
    parser.add_argument("coverage_xml", type=Path)
    args = parser.parse_args(argv)
    totals = parse_cobertura_totals(args.coverage_xml)
    ratio = totals.ratio
    print(f"coverage ratio {ratio:.6f} ({totals.covered}/{totals.valid} lines+branches)")
    if not ratio_meets_floor(totals.covered, totals.valid):
        print(
            f"coverage floor {COVERAGE_FLOOR:.2%} failed: "
            f"{ratio * 100:.3f}% < {COVERAGE_FLOOR * 100:.3f}% "
            f"(raw {totals.covered}/{totals.valid})",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
