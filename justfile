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

# Unit tests
test:
    uv run pytest

# Validate local feeds/data without network
check:
    uv run pg-essay-feeds check

# Offline synthetic update + check (no live network)
smoke:
    uv run python -c "from pathlib import Path; from tests.html_samples import synthetic_index_html; Path('articles.html').write_text(synthetic_index_html(), encoding='utf-8')"
    uv run pg-essay-feeds update --source-file articles.html --force
    uv run pg-essay-feeds check

# Live fetch, reconcile, build, publish
update:
    uv run pg-essay-feeds update

# Full local quality gate
all: lint type test check
