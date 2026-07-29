"""Unit tests for errors module."""

from __future__ import annotations

from pydantic import BaseModel, Field, ValidationError

from paul_graham_essay_feeds.models import (
    ExitCode,
    FeedError,
    UserFacingError,
    exit_code_for_exception,
    format_validation_error,
)


class _Mini(BaseModel):
    n: int = Field(ge=1)


def test_format_validation_error() -> None:
    try:
        _Mini.model_validate({"n": 0})
    except ValidationError as exc:
        msg = format_validation_error(exc)
    assert "Invalid configuration" in msg
    assert "n" in msg


def test_exit_code_mapping() -> None:
    assert exit_code_for_exception(UserFacingError("x", exit_code=ExitCode.NETWORK)) == 3
    assert exit_code_for_exception(FeedError("y")) == 1
    assert exit_code_for_exception(RuntimeError("z")) == 4


def test_typed_user_errors() -> None:
    from paul_graham_essay_feeds.models import (
        ConfigurationError,
        NetworkSourceError,
        VerificationError,
    )

    assert ConfigurationError("a").exit_code is ExitCode.USAGE
    assert VerificationError("b").exit_code is ExitCode.VERIFICATION
    assert NetworkSourceError("c").exit_code is ExitCode.NETWORK
