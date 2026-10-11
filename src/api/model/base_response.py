"""Standard generic response envelope for the API layer."""

from typing import Any

from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    """Structured error information."""

    code: str = Field(
        ...,
        description="Machine-readable error identifier",
        examples=["PLANT_NOT_FOUND"],
    )
    details: dict[str, Any] | None = Field(
        default=None,
        description="Arbitrary supplementary contextual metadata",
        examples=[{"plant_id": "93b3f237-6d2c-47ea-bd50-c83134638706"}],
    )


class BaseResponse[T](BaseModel):
    """Unified API response envelope matching pet-plant web client specification."""

    success: bool = Field(
        ...,
        description="Indicates whether the request was successful",
        examples=[True],
    )
    data: T | None = Field(
        default=None,
        description="Response payload when success is True; null on errors",
    )
    message: str | None = Field(
        default=None,
        description="Human-readable informational message or error notice",
        examples=[None],
    )
    error: ErrorDetail | None = Field(
        default=None,
        description="Error detail object when success is False; null on success",
    )

    @classmethod
    def ok(cls, data: T, message: str | None = None) -> "BaseResponse[T]":
        """Construct a successful envelope wrapping data."""
        return cls(success=True, data=data, message=message, error=None)

    @classmethod
    def fail(
        cls,
        message: str,
        error_code: str,
        details: dict[str, Any] | None = None,
    ) -> "BaseResponse[None]":
        """Construct an error envelope."""
        return BaseResponse[None](
            success=False,
            data=None,
            message=message,
            error=ErrorDetail(code=error_code, details=details),
        )
