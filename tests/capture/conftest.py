"""In-memory SQLite wiring, paired devices and a fake MinIO for the ``capture`` tests.

``capture`` resolves a device's plant through ``registry``, which checks the
device's current owner in ``core``, so the ``auth`` and ``registry`` tables are
created alongside the ``vision`` ones. The MinIO client is swapped on
:mod:`capture.service`; no network is touched.
"""

import uuid
from collections.abc import Callable, Iterator

import pytest
from capture_fakes import FakeStorage
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from capture import models as capture_models  # noqa: F401 - register tables
from capture import service
from capture.api import router as capture_router
from capture.db import Base as VisionBase
from core.db import Base as AuthBase
from core.db import get_session
from core.devices import models as device_models  # noqa: F401 - register tables
from core.devices.dependencies import get_current_device
from core.devices.models import Device
from core.users.dependencies import get_current_active_user
from core.users.models import User
from registry import models as registry_models  # noqa: F401 - register tables
from registry.db import Base as RegistryBase
from registry.db import utcnow
from registry.models import Plant

_BASES: tuple[type[DeclarativeBase], ...] = (AuthBase, RegistryBase, VisionBase)

#: Every plant from :func:`add_plant` belongs to this owner.
OWNER_ID = uuid.uuid4()


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    ).execution_options(schema_translate_map={"auth": None, "registry": None, "vision": None})
    for base in _BASES:
        base.metadata.create_all(engine)
    try:
        yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    finally:
        for base in reversed(_BASES):
            base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    with session_factory() as s:
        yield s


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> FakeStorage:
    fake = FakeStorage()
    monkeypatch.setattr(service, "get_minio_client", lambda: fake)
    return fake


@pytest.fixture
def add_plant(session_factory: sessionmaker[Session]) -> Callable[..., uuid.UUID]:
    def _add(
        device_id: str | None = "cam-1",
        *,
        archived: bool = False,
        owner_id: uuid.UUID = OWNER_ID,
    ) -> uuid.UUID:
        plant = Plant(
            id=uuid.uuid4(),
            owner_id=owner_id,
            name="Spathi",
            device_id=device_id,
            archived_at=utcnow() if archived else None,
        )
        with session_factory() as s:
            s.add(plant)
            s.commit()
        return plant.id

    return _add


@pytest.fixture
def plant_id(add_plant: Callable[..., uuid.UUID]) -> uuid.UUID:
    return add_plant()


@pytest.fixture
def add_device(session_factory: sessionmaker[Session]) -> Callable[..., Device]:
    """A device already paired to ``owner_id`` (skipping the pairing dance)."""

    def _add(physical_id: str = "cam-1", *, owner_id: uuid.UUID = OWNER_ID) -> Device:
        with session_factory() as s:
            if s.get(User, owner_id) is None:
                s.add(
                    User(
                        id=owner_id,
                        email=f"{owner_id}@example.com",
                        name="owner",
                        hashed_password="x",
                        is_active=True,
                    )
                )
                s.flush()
            device = Device(id=uuid.uuid4(), physical_id=physical_id, owner_id=owner_id)
            s.add(device)
            s.commit()
        return device

    return _add


def _user(*, is_superuser: bool) -> User:
    return User(
        id=uuid.uuid4(),
        email="ops@example.com" if is_superuser else "alice@example.com",
        name="ops" if is_superuser else "alice",
        hashed_password="x",
        is_active=True,
        is_superuser=is_superuser,
    )


@pytest.fixture
def admin() -> User:
    return _user(is_superuser=True)


@pytest.fixture
def owner() -> User:
    return _user(is_superuser=False)


@pytest.fixture
def app(session_factory: sessionmaker[Session]) -> FastAPI:
    app = FastAPI()
    app.include_router(capture_router)

    def _override_session() -> Iterator[Session]:
        s = session_factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session] = _override_session
    return app


@pytest.fixture
def client_as(app: FastAPI) -> Iterator[Callable[[User | Device | None], TestClient]]:
    """``client_as(caller)`` → a test client authenticated as a user or a device.

    ``None`` is anonymous: neither a user nor a device token.
    """
    with TestClient(app) as test_client:

        def _as(caller: User | Device | None) -> TestClient:
            app.dependency_overrides.pop(get_current_active_user, None)
            app.dependency_overrides.pop(get_current_device, None)
            if isinstance(caller, Device):
                app.dependency_overrides[get_current_device] = lambda: caller
            elif caller is not None:
                app.dependency_overrides[get_current_active_user] = lambda: caller
            return test_client

        yield _as
