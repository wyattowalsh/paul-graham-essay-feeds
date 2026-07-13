.PHONY: help sync lint type test check smoke all

help:
	@printf '%s\n' \
	  'make sync    uv sync --all-groups' \
	  'make lint    ruff format --check + ruff check' \
	  'make type    ty check' \
	  'make test    pytest' \
	  'make check   pg-essay-feeds check' \
	  'make smoke   offline synthetic update + check' \
	  'make all     lint type test check'

sync:
	uv sync --all-groups

lint:
	uv run ruff format --check .
	uv run ruff check .

type:
	uv run ty check

test:
	uv run pytest

check:
	uv run pg-essay-feeds check

smoke:
	uv run python -c "from pathlib import Path; from tests.html_samples import synthetic_index_html; Path('articles.html').write_text(synthetic_index_html(), encoding='utf-8')"
	uv run pg-essay-feeds update --source-file articles.html --force
	uv run pg-essay-feeds check

all: lint type test check
