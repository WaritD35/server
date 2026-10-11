"""Device pairing and device-token checks — no HTTP concerns here.

Pairing follows the OAuth 2.0 device authorization grant (RFC 8628):

1. :func:`start_pairing` — the device reports its ``physical_id`` and gets a
   secret ``device_code`` plus a short ``user_code`` to show on its display.
2. :func:`approve_pairing` — the signed-in owner types the ``user_code``; the
   device is now theirs.
3. :func:`exchange_device_code` — the device, polling with ``device_code``,
   collects its long-lived token once the owner has approved.

After that the device stays signed in. :func:`revoke_device` is how the owner
stops it; the device is refused from its very next request.
"""

import secrets
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from core.devices.models import Device, DevicePairing
from core.security import hash_opaque_token, new_opaque_token
from core.users.models import User

#: How long a user code stays valid after the device asks for one.
PAIRING_TTL = timedelta(minutes=10)
#: Seconds the device should wait between token polls.
POLL_INTERVAL_SECONDS = 5
#: Tags device tokens so they are recognisable in logs and never mistaken for a JWT.
DEVICE_TOKEN_PREFIX = "ppd_"

# No 0/O, 1/I/L: the code is read off a small display and typed by hand.
_USER_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_USER_CODE_LENGTH = 8


class PairingNotFoundError(Exception):
    """No pending, unexpired pairing has this user code."""


class DeviceOwnedByAnotherUserError(Exception):
    """The device is active under a different account; they must revoke it first."""


class InvalidDeviceCodeError(Exception):
    """Unknown device code, already used, or the device was revoked meanwhile."""


class PairingPendingError(Exception):
    """The owner has not approved yet — keep polling."""


class PairingExpiredError(Exception):
    """The user code expired before the owner approved — start over."""


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    # SQLite hands timestamps back naive; they were written as UTC.
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def normalise_user_code(code: str) -> str:
    """``k7qm-4zpx`` / ``K7QM 4ZPX`` → ``K7QM4ZPX``."""
    return "".join(ch for ch in code.upper() if ch.isalnum())


def format_user_code(code: str) -> str:
    """``K7QM4ZPX`` → ``K7QM-4ZPX`` for display."""
    half = len(code) // 2
    return f"{code[:half]}-{code[half:]}"


def _new_user_code(session: Session) -> str:
    while True:
        code = "".join(
            secrets.choice(_USER_CODE_ALPHABET) for _ in range(_USER_CODE_LENGTH)
        )
        taken = session.scalar(
            select(DevicePairing.id).where(
                DevicePairing.user_code == code, DevicePairing.status == "pending"
            )
        )
        if taken is None:
            return code


# --------------------------------------------------------------------------- #
# pairing
# --------------------------------------------------------------------------- #


def start_pairing(session: Session, physical_id: str) -> tuple[DevicePairing, str]:
    """Open a pairing for ``physical_id``; returns it and the plain device code.

    Any earlier pending pairing for the same device is expired, so only the code
    currently on its display works.
    """
    session.execute(
        update(DevicePairing)
        .where(
            DevicePairing.physical_id == physical_id,
            DevicePairing.status == "pending",
        )
        .values(status="expired")
    )
    device_code = new_opaque_token()
    pairing = DevicePairing(
        physical_id=physical_id,
        user_code=_new_user_code(session),
        device_code_hash=hash_opaque_token(device_code),
        expires_at=_now() + PAIRING_TTL,
    )
    session.add(pairing)
    session.commit()
    session.refresh(pairing)
    return pairing, device_code


def approve_pairing(
    session: Session, user_code: str, *, owner: User, name: str | None = None
) -> Device:
    """Attach the device showing ``user_code`` to ``owner``.

    Re-pairing a device the owner already has (e.g. after a factory reset)
    replaces its token. A device that is active under someone else is refused;
    once revoked, anyone holding it can pair it.
    """
    pairing = session.scalars(
        select(DevicePairing).where(
            DevicePairing.user_code == normalise_user_code(user_code),
            DevicePairing.status == "pending",
        )
    ).one_or_none()
    if pairing is None or _aware(pairing.expires_at) <= _now():
        raise PairingNotFoundError(user_code)

    device = get_device_by_physical_id(session, pairing.physical_id)
    if device is None:
        device = Device(physical_id=pairing.physical_id, owner_id=owner.id, name=name)
        session.add(device)
    else:
        if device.status == "active" and device.owner_id != owner.id:
            raise DeviceOwnedByAnotherUserError(pairing.physical_id)
        device.owner_id = owner.id
        device.status = "active"
        device.token_hash = None  # whatever token was out there stops working
        device.revoked_at = None
        device.paired_at = _now()
        if name is not None:
            device.name = name
    session.flush()

    pairing.status = "approved"
    pairing.device_id = device.id
    session.commit()
    session.refresh(device)
    return device


def exchange_device_code(session: Session, device_code: str) -> tuple[Device, str]:
    """Hand the device its long-lived token once the owner has approved.

    Each approval yields exactly one token: the pairing is consumed here.
    """
    pairing = session.scalars(
        select(DevicePairing).where(
            DevicePairing.device_code_hash == hash_opaque_token(device_code)
        )
    ).one_or_none()
    if pairing is None or pairing.status == "consumed":
        raise InvalidDeviceCodeError()
    if pairing.status == "expired":
        raise PairingExpiredError()
    if pairing.status == "pending":
        if _aware(pairing.expires_at) <= _now():
            pairing.status = "expired"
            session.commit()
            raise PairingExpiredError()
        raise PairingPendingError()

    device = session.get(Device, pairing.device_id)
    if device is None or device.status != "active":
        raise InvalidDeviceCodeError()
    token = new_opaque_token(DEVICE_TOKEN_PREFIX)
    device.token_hash = hash_opaque_token(token)
    pairing.status = "consumed"
    session.commit()
    session.refresh(device)
    return device, token


# --------------------------------------------------------------------------- #
# devices
# --------------------------------------------------------------------------- #


def authenticate_device(session: Session, token: str) -> Device | None:
    """The active device holding ``token``, or ``None``. Records ``last_seen_at``.

    The heartbeat update is throttled: ``last_seen_at`` is only written when it
    is either NULL or older than 5 minutes, avoiding a database write
    transaction on every high-frequency read poll.
    """
    device = session.scalars(
        select(Device).where(
            Device.token_hash == hash_opaque_token(token), Device.status == "active"
        )
    ).one_or_none()
    if device is not None:
        now = _now()
        if device.last_seen_at is None or (now - _aware(device.last_seen_at)) > timedelta(
            minutes=5
        ):
            device.last_seen_at = now
            session.commit()
    return device


def get_device(session: Session, device_id: uuid.UUID) -> Device | None:
    return session.get(Device, device_id)


def get_device_by_physical_id(session: Session, physical_id: str) -> Device | None:
    return session.scalars(
        select(Device).where(Device.physical_id == physical_id)
    ).one_or_none()


def list_devices(
    session: Session, *, owner_id: uuid.UUID | None = None
) -> Sequence[Device]:
    stmt = select(Device).order_by(Device.created_at, Device.id)
    if owner_id is not None:
        stmt = stmt.where(Device.owner_id == owner_id)
    return session.scalars(stmt).all()


def revoke_device(session: Session, device: Device) -> Device:
    """Stop the device: its token is refused from the next request on.

    Idempotent. The row (and its ownership) stays; pairing again re-enables it.
    """
    if device.status != "revoked":
        device.status = "revoked"
        device.token_hash = None
        device.revoked_at = _now()
        session.commit()
        session.refresh(device)
    return device
