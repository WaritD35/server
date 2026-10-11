"""Human-readable messages for the API layer."""


class Message:
    """Standardized response messages for success notices and error explanations."""

    PLANT_NOT_FOUND = "The requested plant was not found or is not accessible."
    DEVICE_NOT_BOUND = "This device is not bound to any plant. Register or bind a plant first."
    UNAUTHORIZED = "Authentication required. Provide a valid device bearer token or user JWT."
    PLANT_ID_REQUIRED = "Query parameter 'plant_id' is required when authenticating as a user."
    FORBIDDEN = "You do not have permission to access this plant."
    INTERNAL_ERROR = "An unexpected error occurred while retrieving companion state."
