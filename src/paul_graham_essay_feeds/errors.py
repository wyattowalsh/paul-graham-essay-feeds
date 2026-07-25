"""User-facing error taxonomy and stable exit codes (ADR-006)."""

from __future__ import annotations

from enum import IntEnum

from pydantic import ValidationError

from paul_graham_essay_feeds.model import FeedError


class ExitCode(IntEnum):
    """Stable process exit codes for automation."""

    SUCCESS = 0
    USAGE = 1
    VERIFICATION = 2
    NETWORK = 3
    INTERNAL = 4


class UserFacingError(FeedError):
    """Expected operational failure with a concise message and exit code."""

    def __init__(self, message: str, *, exit_code: ExitCode = ExitCode.USAGE) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class ConfigurationError(UserFacingError):
    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=ExitCode.USAGE)


class VerificationError(UserFacingError):
    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=ExitCode.VERIFICATION)


class NetworkSourceError(UserFacingError):
    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=ExitCode.NETWORK)


def format_validation_error(exc: ValidationError) -> str:
    """One-line-ish Settings/model validation diagnostic without traceback."""
    errors = exc.errors()
    if not errors:
        return "Invalid configuration."
    parts: list[str] = []
    for err in errors[:5]:
        loc = ".".join(str(x) for x in err.get("loc", ()))
        msg = err.get("msg", "invalid")
        parts.append(f"{loc}: {msg}" if loc else msg)
    extra = len(errors) - len(parts)
    suffix = f" (+{extra} more)" if extra > 0 else ""
    return "Invalid configuration: " + "; ".join(parts) + suffix


def exit_code_for_exception(exc: BaseException) -> int:
    """Map known exceptions to ExitCode integers."""
    if isinstance(exc, UserFacingError):
        return int(exc.exit_code)
    if isinstance(exc, ValidationError):
        return int(ExitCode.USAGE)
    if isinstance(exc, FeedError):
        return int(ExitCode.USAGE)
    return int(ExitCode.INTERNAL)
