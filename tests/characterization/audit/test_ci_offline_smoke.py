"""CI offline smoke must not live-probe essay URLs (validate_links defaults on)."""

from __future__ import annotations

from pathlib import Path


def test_ci_offline_smoke_passes_no_validate_links() -> None:
    yml = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "ci.yml"
    text = yml.read_text(encoding="utf-8")
    assert "--no-validate-links" in text
    assert "--no-enrich" in text
    smoke = text.split("Offline pipeline smoke", 1)[1]
    assert "--no-validate-links" in smoke
