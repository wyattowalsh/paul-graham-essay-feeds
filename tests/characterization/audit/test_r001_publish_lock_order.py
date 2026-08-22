"""RV-R-001: recover_materialize must not run before acquire_write_lock."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from paul_graham_essay_feeds import pipeline


def test_publish_does_not_recover_before_lock() -> None:
    """AST: no recover_materialize call before acquire_write_lock in _publish."""
    source = inspect.getsource(pipeline._publish_catalog_and_feeds)
    tree = ast.parse(source)
    fn = tree.body[0]
    assert isinstance(fn, ast.FunctionDef)

    saw_lock = False
    pre_lock_recover = False
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        name = None
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
        if name == "acquire_write_lock":
            saw_lock = True
        if name == "recover_materialize" and not saw_lock:
            pre_lock_recover = True

    assert saw_lock is True
    assert pre_lock_recover is False


def test_publish_source_mentions_under_lock_recover() -> None:
    """Under-lock recover remains present in source."""
    path = Path(pipeline.__file__)
    text = path.read_text(encoding="utf-8")
    # Locked publish + locked catalog-only write; no pre-lock recover.
    assert text.count("recover_materialize(root)") == 2


def test_catalog_only_save_lock_order() -> None:
    """Catalog-only write: acquire lock, then recover, then save_catalog."""
    source = inspect.getsource(pipeline._save_catalog_under_lock)
    lock_at = source.index("acquire_write_lock(")
    recover_at = source.index("recover_materialize(")
    save_at = source.index("save_catalog(")
    assert lock_at < recover_at < save_at
