#!/usr/bin/env bash
set -Eeuo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

supports_python() {
  "$1" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' \
    >/dev/null 2>&1
}

if [[ -n "${PYTHON:-}" ]]; then
  if ! supports_python "$PYTHON"; then
    printf 'PYTHON=%s is unavailable or older than Python 3.11.\n' "$PYTHON" >&2
    exit 1
  fi
  exec "$PYTHON" update_feed.py "$@"
fi

for candidate in python3.13 python3.12 python3.11 python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 && supports_python "$candidate"; then
    exec "$candidate" update_feed.py "$@"
  fi
done

if command -v uv >/dev/null 2>&1; then
  exec uv run --python 3.13 --locked update_feed.py "$@"
fi

printf 'Python 3.11+ is required. Install it, or install uv, then rerun ./update.sh.\n' >&2
exit 1
