# Paul Graham essay feeds — common tasks (requires `just` + `uv`)

set shell := ["bash", "-euo", "pipefail", "-c"]

default:
    @just --list

# Install all dependency groups
sync:
    uv sync --all-groups

# Format check + lint
lint:
    uv run ruff format --check .
    uv run ruff check .

# Type check
type:
    uv run ty check

# Offline tests (unit + integration + e2e + smoke) + coverage ≥ 90%
test:
    uv run pytest

# Coverage report only (same suite)
cov:
    uv run pytest --cov=paul_graham_essay_feeds --cov-report=term-missing --cov-fail-under=90

# By layer
test-unit:
    uv run pytest tests/unit -m unit -q

test-integration:
    uv run pytest tests/integration -m integration -q

test-e2e:
    uv run pytest tests/e2e -m e2e -q

test-smoke:
    uv run pytest tests/smoke -m smoke -q

# Opt-in live network
test-live:
    uv run pytest -m live -q

# Validate local feeds/data without network
check:
    uv run pg-essay-feeds check

# Offline synthetic update + check (no live network)
smoke:
    #!/usr/bin/env bash
    ROOT="$(mktemp -d)"
    uv run python -c "from pathlib import Path; from tests.html_samples import synthetic_index_html; import sys; Path(sys.argv[1]).write_text(synthetic_index_html(), encoding='utf-8')" "$ROOT/articles.html"
    uv run pg-essay-feeds update --repo-root "$ROOT" --quiet --no-enrich --source-file "$ROOT/articles.html"
    uv run pg-essay-feeds check --repo-root "$ROOT" --quiet

# Live fetch + write feeds
update:
    uv run pg-essay-feeds update

# Full local quality gate
all: lint type test check
