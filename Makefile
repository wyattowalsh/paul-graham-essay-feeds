.PHONY: help sync lint type test check fixture-build all

help:
	@printf '%s\n' \
	  'make sync           uv sync --all-groups' \
	  'make lint           ruff format --check + ruff check' \
	  'make type           ty check' \
	  'make test           pytest' \
	  'make check          pg-essay-feeds check' \
	  'make fixture-build  update from offline HTML fixture' \
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
	  --force

all: lint type test check
