"""Controller layer for the companion API.

Provides HTTP routing, OpenAPI documentation, and dual-mode authentication
(Device Token vs User JWT).
"""

import uuid
from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from api.exception.api_exception import (
    PlantIdRequiredError,
    UnauthorizedError,
)
from api.model.base_response import BaseResponse
from api.model.companion_state import CompanionStateData
from api.service.companion_service import CompanionService
from core.db import get_session
from core.devices.service import authenticate_device
from core.security import decode_access_token
from core.users.service import get_user_by_id

bearer_scheme = HTTPBearer(auto_error=False, scheme_name="CompanionAuth")

router = APIRouter(prefix="/companion", tags=["Companion"])


class CallerIdentity:
    """Represents an authenticated caller (either an edge device or a user)."""

    def __init__(
        self,
        is_device: bool,
        device_physical_id: str | None = None,
        user_id: uuid.UUID | None = None,
    ) -> None:
        self.is_device = is_device
        self.device_physical_id = device_physical_id
        self.user_id = user_id


def get_caller_identity(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: Annotated[Session, Depends(get_session)],
) -> CallerIdentity:
    """Dependency validating either a device bearer token (ppd_...) or a user JWT."""
    if credentials is None or not credentials.credentials:
        raise UnauthorizedError()

    token = credentials.credentials
    if token.startswith("ppd_"):
        device = authenticate_device(session, token)
        if device is None:
            raise UnauthorizedError(message="Invalid or revoked device token")
        return CallerIdentity(is_device=True, device_physical_id=device.physical_id)

    try:
        payload = decode_access_token(token)
        sub = payload.get("sub")
        if sub is None:
            raise UnauthorizedError()
        user_id = uuid.UUID(str(sub))
    except (jwt.PyJWTError, ValueError):
        raise UnauthorizedError() from None

    user = get_user_by_id(session, user_id)
    if user is None or not user.is_active:
        raise UnauthorizedError()

    return CallerIdentity(is_device=False, user_id=user.id)


def get_companion_service() -> CompanionService:
    """Dependency providing a CompanionService instance."""
    return CompanionService()


@router.get(
    "/devices/me/state",
    response_model=BaseResponse[CompanionStateData],
    summary="Get current companion device state",
    description=(
        "Retrieves the real-time synthesized state of the plant companion for "
        "web clients and edge devices.\n\n"
        "### Delivery Modes:\n"
        "1. **Steady / Healthy (`NO_ACTION`)**: Silent timeline update. Green indicator in UI, "
        "cheerful companion check-in card, zero alert popup, `care_plan: null`.\n"
        "2. **Action Needed (`CARE_ADVICE_REQUIRED`)**: High-priority alert banner, interactive "
        "care card with prioritized action checklist, 2–3 word button labels, and 1st-person "
        "plant voice.\n"
        "3. **Fallback Photo Retake (`REQUEST_MORE_INFORMATION`)**: Friendly photo retake prompt "
        "in UI when image quality or consensus falls below threshold (< 0.50), "
        "with `care_plan: null`.\n\n"
        "### Authentication Rules:\n"
        "- **Edge Devices**: Authenticate using Bearer token (`ppd_...`). The plant is resolved "
        "from the device's live binding in the registry.\n"
        "- **Web Clients (Users)**: Authenticate using Bearer JWT. `plant_id` query parameter "
        "is mandatory, and the user must be the registered owner."
    ),
    responses={
        200: {
            "description": "Companion state retrieved successfully.",
            "model": BaseResponse[CompanionStateData],
        },
        400: {
            "description": (
                "Bad Request — missing mandatory plant_id parameter for user authentication."
            ),
            "model": BaseResponse[None],
        },
        401: {
            "description": "Unauthorized — missing, invalid, or revoked credentials.",
            "model": BaseResponse[None],
        },
        403: {
            "description": "Forbidden — user does not have permission to access this plant.",
            "model": BaseResponse[None],
        },
        404: {
            "description": "Not Found — plant does not exist or device is not bound to any plant.",
            "model": BaseResponse[None],
        },
        500: {
            "description": "Internal Server Error — unexpected internal error.",
            "model": BaseResponse[None],
        },
    },
)
def get_companion_device_state(
    session: Annotated[Session, Depends(get_session)],
    caller: Annotated[CallerIdentity, Depends(get_caller_identity)],
    service: Annotated[CompanionService, Depends(get_companion_service)],
    plant_id: Annotated[
        uuid.UUID | None,
        Query(
            description="Target plant ID (required for user JWT, optional for edge device)",
            examples=["93b3f237-6d2c-47ea-bd50-c83134638706"],
        ),
    ] = None,
) -> BaseResponse[CompanionStateData]:
    """Retrieve the current companion state for the caller."""
    if caller.is_device:
        if caller.device_physical_id is None:
            raise UnauthorizedError(message="Invalid device identity")
        target_plant_id = service.resolve_plant_for_device(
            session, caller.device_physical_id
        )
    else:
        if plant_id is None:
            raise PlantIdRequiredError()
        if caller.user_id is None:
            raise UnauthorizedError(message="Invalid user identity")
        service.verify_plant_access(session, plant_id, caller.user_id)
        target_plant_id = plant_id

    data = service.get_companion_state(session, target_plant_id)
    return BaseResponse.ok(data)
