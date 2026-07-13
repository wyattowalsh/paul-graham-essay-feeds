#!/usr/bin/env bash
set -u
ROOT="$(cd -- "$(dirname -- "$0")" && pwd)"
cd "$ROOT"
./START_CODEX.sh
status=$?
printf '\nPress Return to close.\n'
read -r
exit "$status"
