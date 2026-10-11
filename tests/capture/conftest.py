"""In-memory SQLite wiring, a fake webcam and a fake MinIO for the ``capture`` tests.

``capture`` resolves plants through ``registry``'s interface, so the ``registry``
table is created alongside the ``vision`` ones. The webcam and the MinIO client
are swapped on :mod:`capture.service`; no camera or network is touched.
"""

import uuid
from collections.abc import Callable, Iterator

import pytest
from capture_fakes import FakeStorage, FakeWebcam
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from capture import models as capture_models  # noqa: F401 - register tables
from capture import service
from capture.api import router as capture_router
from capture.db import Base as VisionBase
from core.config import get_settings
from core.db import get_session
from core.users.dependencies import get_current_active_user
from core.users.models import User
from registry import models as registry_models  # noqa: F401 - register tables
from registry.db import Base as RegistryBase
from registry.db import utcnow
from registry.models import Plant

_BASES: tuple[type[DeclarativeBase], ...] = (RegistryBase, VisionBase)


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    ).execution_options(schema_translate_map={"registry": None, "vision": None})
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
def set_webcam_map(monkeypatch: pytest.MonkeyPatch) -> Callable[[str], None]:
    """``set_webcam_map("cam-1=0")`` → what ``CAPTURE_WEBCAM_MAP`` reads as."""
    settings = get_settings()

    def _set(raw: str) -> None:
        patched = settings.model_copy(update={"capture_webcam_map": raw})
        monkeypatch.setattr(service, "get_settings", lambda: patched)

    return _set


@pytest.fixture(autouse=True)
def _default_webcam_map(set_webcam_map: Callable[[str], None]) -> None:
    set_webcam_map("cam-1=0,cam-2=1")


@pytest.fixture
def webcam(monkeypatch: pytest.MonkeyPatch) -> FakeWebcam:
    fake = FakeWebcam()
    monkeypatch.setattr(service, "capture_once", fake)
    return fake


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> FakeStorage:
    fake = FakeStorage()
    monkeypatch.setattr(service, "get_minio_client", lambda: fake)
    return fake


@pytest.fixture
def add_plant(session_factory: sessionmaker[Session]) -> Callable[..., uuid.UUID]:
    def _add(device_id: str | None = "cam-1", *, archived: bool = False) -> uuid.UUID:
        plant = Plant(
            id=uuid.uuid4(),
            owner_id=uuid.uuid4(),
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
def client_as(app: FastAPI) -> Iterator[Callable[[User | None], TestClient]]:
    """``client_as(user)`` → a test client authenticated as ``user`` (``None``: anonymous)."""
    with TestClient(app) as test_client:

        def _as(user: User | None) -> TestClient:
            if user is None:
                app.dependency_overrides.pop(get_current_active_user, None)
            else:
                app.dependency_overrides[get_current_active_user] = lambda: user
            return test_client

        yield _as
