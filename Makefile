.PHONY: help sync lint type test check fixture-build baseline-test notebook-check all

help:
	@printf '%s\n' \
	  'make sync           uv sync --all-groups' \
	  'make lint           ruff format --check + ruff check' \
	  'make type           ty check' \
	  'make test           pytest' \
	  'make check          pg-essay-feeds check' \
	  'make fixture-build  update from fixture with example public URL' \
	  'make baseline-test  run preserved RSS baseline unittest' \
	  'make notebook-check verify Colab notebook JSON parses' \
	  'make all            lint type test check'

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

fixture-build:
	uv run pg-essay-feeds update \
	  --source-file fixtures/articles-2026-07-11.fragment.html \
	  --public-base-url https://paul-graham-essay-feeds.vercel.app/ \
	  --force

baseline-test:
	cd reference/rss2-baseline && python3 -m unittest -v

notebook-check:
	uv run python -c "import json; json.load(open('notebooks/regenerate_feeds.ipynb')); print('notebook OK')"

all: lint type test check
