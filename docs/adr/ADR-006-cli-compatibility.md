# ADR-006: CLI UX, exit codes, Python support

**Status:** Accepted  
**Date:** 2026-07-25  
**Fixes:** F-010, F-012, F-014, F-054

## Python

- Support **3.12, 3.13, 3.14**.
- Lower `requires-python` to `>=3.12` after matrix proof.
- Ship `py.typed` (PEP 561).

## Commands

Preserve `update` and `check`. Add (Wave 3): `plan`, `diff`, `status`, `migrate`, `version` and flags `--dry-run`, `--offline`, `--full`, `--canary`, `--refresh-budget`, `--format human|json`, `--quiet`, `--verbose`, `--public-base-url`.

## Output

- Machine JSON → stdout; human diagnostics/progress → stderr.
- Successful `--quiet` emits **zero bytes** to both streams.
- Progress centralized and disabled for non-TTY / machine / quiet modes.

## Errors

- Construct Settings **inside** the command boundary.
- Expected failures: concise message, stable exit code, **no traceback**.
- Unexpected errors: visible; debug mode keeps chained traceback.

## Exit codes (initial)

| Code | Class |
| ---: | :--- |
| 0 | Success |
| 1 | Usage / validation / expected operational failure |
| 2 | Verification / publication integrity failure |
| 3 | Network / source failure |
| 4 | Unexpected internal error |

Flags override Settings only when explicitly passed (`ParameterSource`).
