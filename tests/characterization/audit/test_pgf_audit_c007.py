"""T-C007 / RV-C-007: check self-URLs come from Settings, not Catalog JSON."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from paul_graham_essay_feeds.feeds import verify_feed_artifacts
from paul_graham_essay_feeds.models import Catalog
from paul_graham_essay_feeds.verify import _load_catalog_hints

_REPO = Path(__file__).resolve().parents[3]
_VERIFY = _REPO / "src" / "paul_graham_essay_feeds" / "verify.py"
_CLI = _REPO / "src" / "paul_graham_essay_feeds" / "cli.py"


def test_verify_feed_artifacts_frozen_signature() -> None:
    params = inspect.signature(verify_feed_artifacts).parameters
    assert tuple(params) == ("root", "min_items", "public_base_url")
    assert params["root"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert params["min_items"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["public_base_url"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["public_base_url"].default is None


def test_catalog_rejects_public_base_url_key() -> None:
    with pytest.raises(ValidationError, match="extra"):
        Catalog.model_validate(
            {
                "schema_version": 1,
                "material_config_fingerprint": "test",
                "entry_order": [],
                "entries": {},
                "public_base_url": "https://example.com/feeds/",
            }
        )


def test_load_catalog_hints_keeps_entry_order_ignores_public_base_url(tmp_path: Path) -> None:
    order = ["https://paulgraham.com/a.html"]
    (tmp_path / "catalog.json").write_text(
        json.dumps({"entry_order": order, "public_base_url": "https://example.com/feeds/"}),
        encoding="utf-8",
    )
    assert _load_catalog_hints(tmp_path) == order
    start = _VERIFY.read_text(encoding="utf-8").index("def _load_catalog_hints")
    rest = _VERIFY.read_text(encoding="utf-8")[start:]
    body = rest[: rest.index("\ndef ", 1)]
    assert 'payload.get("public_base_url")' not in body
    assert 'payload.get("entry_order")' in body


def test_check_cmd_passes_settings_public_base_url() -> None:
    src = _CLI.read_text(encoding="utf-8")
    start = src.index("def check_cmd")
    body = src[start : src.index("\ndef ", start + 1)]
    assert "public_base_url=settings.public_base_url" in body
    assert "verify_feed_artifacts(" in body
