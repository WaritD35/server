"""Custom exceptions for the API layer."""

from typing import Any

from api.constant.error_code import ErrorCode
from api.constant.message import Message
from api.constant.status_code import StatusCode


class ApiException(Exception):
    """Base class for all business and routing exceptions raised in api/."""

    def __init__(
        self,
        status_code: int = StatusCode.INTERNAL_SERVER_ERROR,
        error_code: str = ErrorCode.INTERNAL_SERVER_ERROR,
        message: str = Message.INTERNAL_ERROR,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.message = message
        self.details = details


class PlantNotFoundError(ApiException):
    """Raised when the specified plant is missing or not registered."""

    def __init__(self, plant_id: Any) -> None:
        super().__init__(
            status_code=StatusCode.NOT_FOUND,
            error_code=ErrorCode.PLANT_NOT_FOUND,
            message=Message.PLANT_NOT_FOUND,
            details={"plant_id": str(plant_id)},
        )


class DeviceNotBoundError(ApiException):
    """Raised when an authenticated edge device has no active bound plant."""

    def __init__(self, device_id: str) -> None:
        super().__init__(
            status_code=StatusCode.NOT_FOUND,
            error_code=ErrorCode.DEVICE_NOT_BOUND,
            message=Message.DEVICE_NOT_BOUND,
            details={"device_id": device_id},
        )


class UnauthorizedError(ApiException):
    """Raised when request lacks valid authentication credentials."""

    def __init__(self, message: str = Message.UNAUTHORIZED) -> None:
        super().__init__(
            status_code=StatusCode.UNAUTHORIZED,
            error_code=ErrorCode.UNAUTHORIZED,
            message=message,
        )


class PlantIdRequiredError(ApiException):
    """Raised when a user authenticates without supplying mandatory plant_id."""

    def __init__(self) -> None:
        super().__init__(
            status_code=StatusCode.BAD_REQUEST,
            error_code=ErrorCode.PLANT_ID_REQUIRED,
            message=Message.PLANT_ID_REQUIRED,
        )


class ForbiddenError(ApiException):
    """Raised when an authenticated user does not own the requested plant."""

    def __init__(self, plant_id: Any) -> None:
        super().__init__(
            status_code=StatusCode.FORBIDDEN,
            error_code=ErrorCode.FORBIDDEN,
            message=Message.FORBIDDEN,
            details={"plant_id": str(plant_id)},
        )
