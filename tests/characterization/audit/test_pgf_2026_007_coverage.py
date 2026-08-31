"""PGF-2026-007: coverage precision ≥2 and raw coverage.xml ratio floor."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from tests.coverage_xml import (
    COVERAGE_FLOOR,
    main,
    parse_cobertura_totals,
    ratio_meets_floor,
)

_REPO = Path(__file__).resolve().parents[3]


def _cobertura_xml(
    *,
    lines_covered: int,
    lines_valid: int,
    branches_covered: int,
    branches_valid: int,
) -> str:
    return (
        '<?xml version="1.0" ?>\n'
        "<coverage"
        f' lines-covered="{lines_covered}" lines-valid="{lines_valid}"'
        f' branches-covered="{branches_covered}" branches-valid="{branches_valid}"'
        ' line-rate="0" branch-rate="0" complexity="0"'
        ' version="7.10.0" timestamp="1">\n'
        "</coverage>\n"
    )


@pytest.fixture
def coverage_xml_89955(tmp_path: Path) -> Path:
    """89.955% combined lines+branches (must fail a 90.000% floor)."""

    path = tmp_path / "coverage.xml"
    # 71964+17991 = 89955; 80000+20000 = 100000 → 0.89955
    path.write_text(
        _cobertura_xml(
            lines_covered=71_964,
            lines_valid=80_000,
            branches_covered=17_991,
            branches_valid=20_000,
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def coverage_xml_90000(tmp_path: Path) -> Path:
    """90.000% combined lines+branches (must pass)."""

    path = tmp_path / "coverage.xml"
    path.write_text(
        _cobertura_xml(
            lines_covered=72_000,
            lines_valid=80_000,
            branches_covered=18_000,
            branches_valid=20_000,
        ),
        encoding="utf-8",
    )
    return path


def test_ratio_helper_rejects_89955() -> None:
    assert not ratio_meets_floor(89_955, 100_000)
    assert ratio_meets_floor(90_000, 100_000)
    # coverage.py fail_under uses round(total, precision). precision=1 hides
    # 89.95%; precision=2 keeps it below 90.
    assert round(89.95, 1) == 90.0
    assert round(89.95, 2) == 89.95


def test_ratio_helper_rejects_coverage_xml_89955(coverage_xml_89955: Path) -> None:
    totals = parse_cobertura_totals(coverage_xml_89955)
    assert totals.covered == 89_955
    assert totals.valid == 100_000
    assert totals.ratio == pytest.approx(0.89955)
    assert not ratio_meets_floor(totals.covered, totals.valid, COVERAGE_FLOOR)
    assert main([str(coverage_xml_89955)]) == 1


def test_ratio_helper_accepts_coverage_xml_90000(coverage_xml_90000: Path) -> None:
    totals = parse_cobertura_totals(coverage_xml_90000)
    assert totals.covered == 90_000
    assert totals.valid == 100_000
    assert totals.ratio == pytest.approx(0.90)
    assert ratio_meets_floor(totals.covered, totals.valid, COVERAGE_FLOOR)
    assert main([str(coverage_xml_90000)]) == 0


def test_pyproject_coverage_precision_and_fail_under() -> None:
    data = tomllib.loads((_REPO / "pyproject.toml").read_text(encoding="utf-8"))
    report = data["tool"]["coverage"]["report"]
    assert int(report["precision"]) >= 2
    assert float(report["fail_under"]) == 90.0


def test_ci_gates_raw_coverage_xml_ratio() -> None:
    text = (_REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    tests = text.split("name: Tests", 1)[1]
    assert "--cov-report=xml:coverage.xml" in tests
    assert "python -m tests.coverage_xml coverage.xml" in tests
    xml_gate = tests.split("coverage.xml", 1)[1]
    assert "python -m tests.coverage_xml coverage.xml" in xml_gate
