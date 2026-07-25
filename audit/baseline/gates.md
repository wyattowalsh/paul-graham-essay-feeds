# Baseline gate results

**HEAD:** `60ba457f45a4b738477cfe312a32507176991a45`  
**Recorded:** 2026-07-25  
**Python under uv run:** 3.13.14 (system also has 3.12.2)

| Command | Exit | Notes |
| :--- | ---: | :--- |
| `uv sync --locked --all-groups` | 0 | OK |
| `uv run ruff format --check .` | 0 | 34 files already formatted |
| `uv run ruff check .` | 0 | All checks passed |
| `uv run ty check` | 0 | All checks passed |
| `uv run pytest` | 0 | 171 passed, 1 live deselected; cov **90.44%** |
| `uv run pg-essay-feeds check --quiet` | 0 | 0 stdout/stderr bytes |
| `uv build --no-sources` | 0 | sdist + wheel built |
| Wheel contains `py.typed` | **fail** | **F-011 confirmed** |

Logs: `gates-lint.log`, `gates-pytest.log`, `gates-build.log`.
