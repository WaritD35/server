"""Machine-readable error codes for the API layer."""


class ErrorCode:
    """Standard error codes populated into BaseResponse.error.code."""

    INTERNAL_SERVER_ERROR = "INTERNAL_SERVER_ERROR"
    PLANT_NOT_FOUND = "PLANT_NOT_FOUND"
    DEVICE_NOT_BOUND = "DEVICE_NOT_BOUND"
    UNAUTHORIZED = "UNAUTHORIZED"
    PLANT_ID_REQUIRED = "PLANT_ID_REQUIRED"
    FORBIDDEN = "FORBIDDEN"
    INVALID_TOKEN = "INVALID_TOKEN"
