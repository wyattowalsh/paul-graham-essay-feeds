"""PGF-AUDIT-001: DOCS tag-protection recipe is PUT without creation."""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_DOCS = (_REPO / ".github" / "DOCS.md").read_text(encoding="utf-8")


def _tag_protection_section() -> str:
    start = _DOCS.index("Tag protection (separate ruleset")
    end = _DOCS.index("## Troubleshooting")
    return _DOCS[start:end]


def _ad006_section() -> str:
    start = _DOCS.index("### AD-006")
    end = _DOCS.index("### AD-007")
    return _DOCS[start:end]


def test_docs_tag_example_json_has_no_creation_rule() -> None:
    section = _tag_protection_section()
    assert '"type": "creation"' not in section
    assert '{ "type": "update" }' in section
    assert '{ "type": "deletion" }' in section
    assert '{ "type": "non_fast_forward" }' in section


def test_docs_tag_recipe_is_put_not_recreate() -> None:
    section = _tag_protection_section()
    assert "--method PUT" in section
    assert "rulesets/22371020" in section
    assert "--method POST" not in section
    assert "recreate-with-empty-bypass" not in section
    assert "Agents must not PUT" in section


def test_ad006_cmdline_or_none_includes_bootstrap_fallback() -> None:
    ad006 = _ad006_section()
    assert "_cmdline_or_none" in ad006
    assert "bootstrap-fallback" in ad006
    assert "--allow-bootstrap-fallback" in ad006
    assert "--no-allow-bootstrap-fallback" in ad006
