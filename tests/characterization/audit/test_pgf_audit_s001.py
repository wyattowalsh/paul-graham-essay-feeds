"""T-S001 / RV-S-001: updater push must not persist a token in origin."""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_UPDATE = (_REPO / ".github" / "workflows" / "update-feeds.yml").read_text(encoding="utf-8")
_NEXT_JOB = re.compile(r"\n  [A-Za-z][\w-]*:")


def _job_block(text: str, job: str) -> str:
    marker = f"\n  {job}:"
    start = text.find(marker)
    assert start >= 0, f"job {job!r} not found"
    rest = text[start + len(marker) :]
    match = _NEXT_JOB.search(rest)
    return rest[: match.start()] if match else rest


def test_update_feeds_workflow_has_no_token_in_origin_url() -> None:
    """W0 S001: workflow text must not contain token-in-URL credentials."""
    assert "x-access-token:" not in _UPDATE
    assert "set_push_remote" not in _UPDATE


def test_update_feeds_push_uses_ephemeral_bearer_extraheader() -> None:
    """Origin stays tokenless; bearer extraheader is push-scoped and unset."""
    publish = _job_block(_UPDATE, "publish")
    assert "persist-credentials: false" in publish
    assert 'url = f"https://github.com/{github_repo}.git"' in publish
    assert '"http.https://github.com/.extraheader="' in publish
    assert 'f"http.https://github.com/.extraheader=AUTHORIZATION: bearer {token}"' in publish
    assert "--unset-all" in publish
    assert "http.https://github.com/.extraheader" in publish
    assert "push_with_ephemeral_auth" in publish
    assert "build_push_argv" in publish
    assert "validate_push_argv" in publish
    assert "force-with-lease" not in _UPDATE
    assert "print(token)" not in publish
    assert "print(cmd)" not in publish
    assert "print(argv)" not in publish
    assert "capture_output=True" in publish
