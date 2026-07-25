# Paul Graham essay feeds — local tasks (requires `just` + `uv`)
# Run `just` or `just help` for grouped recipes and aliases.
# Docs: README.md (users), DOCS.md (developers), AGENTS.md (agents).

set minimum-version := '1.52.0'
set shell := ["bash", "-euo", "pipefail", "-c"]
set dotenv-load

# ---------------------------------------------------------------------------- #
#                                DEPENDENCIES                                  #
# ---------------------------------------------------------------------------- #

# https://docs.astral.sh/uv/
uv := require("uv")

# ---------------------------------------------------------------------------- #
#                                   HELP                                       #
# ---------------------------------------------------------------------------- #

# Show grouped recipes, docs, and aliases
[doc("Show grouped recipes, docs, and aliases")]
@default:
    echo '{{ BOLD }}Paul Graham essay feeds{{ NORMAL }}'
    echo 'Unofficial metadata-only RSS / Atom / JSON Feed tooling'
    echo ''
    echo '{{ CYAN }}Shortcuts{{ NORMAL }}  a→all  b→build  c→check  l→lint  t→test  ty→type  u→update  s→sync'
    echo '{{ CYAN }}Quality{{ NORMAL }}    just all   →  lint → type → test → check'
    echo '{{ CYAN }}Help{{ NORMAL }}       just / just help / just --list / just --list --group test'
    echo ''
    just --list --list-heading $'{{ BOLD }}Recipes{{ NORMAL }} (aliases on the right):\n'
alias h := default
alias help := default

# ---------------------------------------------------------------------------- #
#                                   SETUP                                      #
# ---------------------------------------------------------------------------- #

# Install all uv dependency groups into .venv
[group("setup")]
[doc("Install all uv dependency groups into .venv")]
@sync:
    {{ uv }} sync --all-groups
alias s := sync
alias i := sync

# ---------------------------------------------------------------------------- #
#                                  CHECKS                                      #
# ---------------------------------------------------------------------------- #

# Ruff format check + lint (no writes)
[group("checks")]
[doc("Ruff format check + lint (no writes)")]
@lint:
    {{ uv }} run ruff format --check .
    {{ uv }} run ruff check .
alias l := lint

# Type check with ty
[group("checks")]
[doc("Type check with ty")]
@type:
    {{ uv }} run ty check
alias ty := type
alias types := type

# Validate committed feeds/ without network
[group("checks")]
[doc("Validate committed feeds/ without network")]
@check:
    {{ uv }} run pg-essay-feeds check
alias c := check

# Full local quality gate: lint → type → test → check
[group("checks")]
[doc("Full local quality gate: lint → type → test → check")]
all: lint type test check
alias a := all

# Offline CI mirror: sync lock, lint, type, test, quiet check, build
[group("checks")]
[doc("Offline CI mirror: locked sync, lint, type, test, quiet check, wheel build")]
ci-local: sync
    {{ uv }} run ruff format --check .
    {{ uv }} run ruff check .
    {{ uv }} run ty check
    {{ uv }} run pytest
    {{ uv }} run pg-essay-feeds check --quiet
    {{ uv }} build --no-sources
alias cil := ci-local

# ---------------------------------------------------------------------------- #
#                                   TEST                                       #
# ---------------------------------------------------------------------------- #

# Offline tests (unit + integration + e2e + smoke) + coverage ≥ 90%
[group("test")]
[doc("Offline tests (unit + integration + e2e + smoke) + coverage ≥ 90%")]
@test *args:
    {{ uv }} run pytest {{ args }}
alias t := test

# Coverage report only (same suite, fail-under 90%)
[group("test")]
[doc("Coverage report only (same suite, fail-under 90%)")]
@cov *args:
    {{ uv }} run pytest --cov=paul_graham_essay_feeds --cov-report=term-missing --cov-fail-under=90 {{ args }}
alias coverage := cov

# Unit tests only
[group("test")]
[doc("Unit tests only")]
@test-unit:
    {{ uv }} run pytest tests/unit -m unit -q
alias tu := test-unit

# Integration tests only
[group("test")]
[doc("Integration tests only")]
@test-integration:
    {{ uv }} run pytest tests/integration -m integration -q
alias ti := test-integration

# End-to-end CLI tests only
[group("test")]
[doc("End-to-end CLI tests only")]
@test-e2e:
    {{ uv }} run pytest tests/e2e -m e2e -q
alias te := test-e2e

# Smoke-marked pytest tests only
[group("test")]
[doc("Smoke-marked pytest tests only")]
@test-smoke:
    {{ uv }} run pytest tests/smoke -m smoke -q
alias tsm := test-smoke

# Opt-in live network tests (hits paulgraham.com)
[group("test")]
[doc("Opt-in live network tests (hits paulgraham.com)")]
@test-live:
    {{ uv }} run pytest -m live -q
alias tl := test-live

# ---------------------------------------------------------------------------- #
#                                   FEEDS                                      #
# ---------------------------------------------------------------------------- #

# Offline synthetic update + check (temp root, no live network)
[group("feeds")]
[doc("Offline synthetic update + check (temp root, no live network)")]
[script("bash")]
smoke:
    set -euo pipefail
    ROOT="$(mktemp -d)"
    {{ uv }} run python -c "from pathlib import Path; from tests.html_samples import synthetic_index_html; import sys; Path(sys.argv[1]).write_text(synthetic_index_html(), encoding='utf-8')" "$ROOT/articles.html"
    {{ uv }} run pg-essay-feeds update --repo-root "$ROOT" --quiet --no-enrich --source-file "$ROOT/articles.html"
    {{ uv }} run pg-essay-feeds check --repo-root "$ROOT" --quiet
alias sm := smoke

# Live fetch + write feeds/ (uses PG_ESSAY_FEEDS_* / .env)
[group("feeds")]
[doc("Live fetch + write feeds/ (uses PG_ESSAY_FEEDS_* / .env)")]
@update:
    {{ uv }} run pg-essay-feeds update
alias u := update

# ---------------------------------------------------------------------------- #
#                                   BUILD                                      #
# ---------------------------------------------------------------------------- #

# Build sdist+wheel and smoke console entry from the wheel
[group("build")]
[doc("Build sdist+wheel and smoke console entry from the wheel")]
[script("bash")]
build:
    set -euo pipefail
    {{ uv }} build --no-sources
    wheel="$(ls -1 dist/*.whl | sort | tail -n 1)"
    {{ uv }} run --isolated --no-project --with "${wheel}" pg-essay-feeds --help
alias b := build
