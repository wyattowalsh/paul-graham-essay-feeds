"""RV-R-001: recover_materialize must not run before acquire_write_lock."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from paul_graham_essay_feeds import pipeline


def test_publish_does_not_recover_before_lock() -> None:
    """AST: no recover_materialize call before acquire_write_lock in finalize."""
    source = inspect.getsource(pipeline._finalize_under_lock)
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
    """Under-lock recover remains present in source (single critical section)."""
    path = Path(pipeline.__file__)
    text = path.read_text(encoding="utf-8")
    # One recover call in _finalize_under_lock; wrappers do not recover again.
    assert text.count("recover_materialize(root)") == 1


def test_catalog_only_save_lock_order() -> None:
    """Writer critical section: acquire lock, then recover, then save/materialize."""
    source = inspect.getsource(pipeline._finalize_under_lock)
    lock_at = source.index("acquire_write_lock(")
    recover_at = source.index("recover_materialize(")
    save_at = source.index("save_catalog(")
    stage_at = source.index("_stage_and_materialize(")
    release_at = source.index("release_write_lock(")
    assert lock_at < recover_at < save_at
    assert recover_at < stage_at < release_at
    assert save_at < release_at
