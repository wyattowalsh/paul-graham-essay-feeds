"""Preflight: v* tag create is allowed iff the ruleset has no creation rule.

Exit 0 when ``creation`` is absent. Exit 2 when ``creation`` is present.
Unknown JSON fields are ignored. Network I/O happens only with ``--live``
(GET of ruleset 22371020). Operators run ``--live`` after PUT; do not add
``--live`` to ``just ci-local``. Agents must not PUT.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Final, NoReturn

RULESET_ID: Final[int] = 22371020
RULESET_GET: Final[str] = f"repos/wyattowalsh/paul-graham-essay-feeds/rulesets/{RULESET_ID}"
EXIT_OK: Final[int] = 0
EXIT_ERROR: Final[int] = 1
EXIT_CREATION_BLOCKED: Final[int] = 2


class _Parser(argparse.ArgumentParser):
    """Usage errors are exit 1 so they are not confused with a creation block."""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        raise SystemExit(EXIT_ERROR)


def creation_rule_present(payload: Any) -> bool:
    """Return True iff ``rules`` contains a ``creation`` rule object."""
    if not isinstance(payload, dict):
        raise ValueError("ruleset JSON root must be an object")
    rules = payload.get("rules")
    if rules is None:
        return False
    if not isinstance(rules, list):
        raise ValueError("ruleset JSON rules must be a list")
    return any(isinstance(rule, dict) and rule.get("type") == "creation" for rule in rules)


def load_ruleset_json(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    return json.loads(text)


def fetch_live_ruleset() -> Any:
    proc = subprocess.run(
        ["gh", "api", RULESET_GET],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip() or f"exit {proc.returncode}"
        raise RuntimeError(f"gh api GET {RULESET_GET} failed: {detail}")
    return json.loads(proc.stdout)


def evaluate(payload: Any) -> int:
    if creation_rule_present(payload):
        print("creation rule present: tag create is blocked", file=sys.stderr)
        return EXIT_CREATION_BLOCKED
    print("creation rule absent: tag create is allowed")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        description=(
            "Exit 2 if the v* tag ruleset includes a creation rule; exit 0 if creation is absent."
        )
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--ruleset-json",
        type=Path,
        help="Local GitHub ruleset JSON (offline tests)",
    )
    source.add_argument(
        "--live",
        action="store_true",
        help="GET ruleset 22371020 via gh api (operator-only; no PUT)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.live:
            payload = fetch_live_ruleset()
        else:
            path = args.ruleset_json
            if path is None:
                raise ValueError("missing --ruleset-json")
            payload = load_ruleset_json(path)
        return evaluate(payload)
    except (OSError, json.JSONDecodeError, RuntimeError, ValueError) as exc:
        print(f"{exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
