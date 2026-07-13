#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if ! command -v codex >/dev/null 2>&1; then
  printf '%s\n' "Codex CLI was not found in PATH." >&2
  printf '%s\n' "Open CODEX_KICKOFF_PROMPT.md and paste it into Codex from this repository root." >&2
  exit 127
fi

exec codex "$(cat CODEX_KICKOFF_PROMPT.md)"
