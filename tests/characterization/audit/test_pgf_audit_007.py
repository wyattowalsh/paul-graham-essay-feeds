"""T-007: pin hatchling in [build-system] requires (rebuild-reproducible backend)."""

from __future__ import annotations

import tomllib
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]


def test_build_system_requires_pins_hatchling() -> None:
    data = tomllib.loads((_REPO / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["build-system"]["requires"] == ["hatchling==1.32.0"]
    assert data["build-system"]["build-backend"] == "hatchling.build"
