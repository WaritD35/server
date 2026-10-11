"""Test fixtures and database configuration for API layer tests."""

import secrets
import uuid
from collections.abc import Callable, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

import action.models  # noqa: F401 - register tables
import advice.models  # noqa: F401 - register tables
import assessment.models  # noqa: F401 - register tables
import companion.models  # noqa: F401 - register tables
import core.devices.models  # noqa: F401 - register tables
import core.users.models  # noqa: F401 - register tables
import knowledge.models  # noqa: F401 - register tables
import orchestrator.models  # noqa: F401 - register tables
import registry.models  # noqa: F401 - register tables
from action.db import Base as ActionBase
from advice.db import Base as AdviceBase
from assessment.db import Base as AssessmentBase
from companion.db import Base as CompanionBase
from core.db import Base as AuthBase
from core.db import get_session
from core.devices.models import Device
from core.security import create_access_token, hash_opaque_token
from core.users.models import User
from knowledge.db import Base as KnowledgeBase
from orchestrator.db import Base as OrchestratorBase
from registry.db import Base as RegistryBase
from registry.models import Plant

_BASES: tuple[type[DeclarativeBase], ...] = (
    AuthBase,
    RegistryBase,
    KnowledgeBase,
    AssessmentBase,
    AdviceBase,
    ActionBase,
    CompanionBase,
    OrchestratorBase,
)


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    """Isolated in-memory SQLite database mapping PostgreSQL schemas to default."""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    ).execution_options(
        schema_translate_map={
            "auth": None,
            "registry": None,
            "knowledge": None,
            "orchestrator": None,
            "assessment": None,
            "advice": None,
            "companion": None,
            "action": None,
        }
    )
    for base in _BASES:
        base.metadata.create_all(engine)
    try:
        yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    finally:
        for base in reversed(_BASES):
            base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def app(session_factory: sessionmaker[Session]) -> FastAPI:
    """FastAPI application configured with in-memory test session override."""
    from fastapi import Request
    from fastapi.responses import JSONResponse

    from api.controller import router as companion_router
    from api.exception import ApiException
    from api.model import BaseResponse

    openapi_tags = [
        {
            "name": "Companion",
            "description": (
                "Character dialogue, gamification status, plant voice, and care status "
                "for web clients and edge devices."
            ),
        }
    ]

    test_app = FastAPI(
        title="Pet-Plant API",
        version="0.1.0",
        summary="Cloud backend for Pet-Plant.",
        openapi_tags=openapi_tags,
    )

    @test_app.exception_handler(ApiException)
    async def api_exception_handler(_request: Request, exc: ApiException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=BaseResponse.fail(
                message=exc.message,
                error_code=exc.error_code,
                details=exc.details,
            ).model_dump(mode="json"),
        )

    test_app.include_router(companion_router)

    def _override_session() -> Iterator[Session]:
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = _override_session
    return test_app


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """TestClient against configured FastAPI application."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def add_user(session_factory: sessionmaker[Session]) -> Callable[..., User]:
    """Seed a test user."""

    def _add(email: str = "alice@example.com") -> User:
        user = User(
            id=uuid.uuid4(),
            email=email,
            name=email.split("@")[0].capitalize(),
            hashed_password="hashed_test_password",
            is_active=True,
            is_superuser=False,
        )
        with session_factory() as session:
            session.add(user)
            session.commit()
            session.refresh(user)
        return user

    return _add


@pytest.fixture
def add_device(session_factory: sessionmaker[Session]) -> Callable[..., tuple[Device, str]]:
    """Seed a test edge device with an active token starting with ppd_."""

    def _add(owner: User, physical_id: str = "device_test_serial_123") -> tuple[Device, str]:
        raw_token = f"ppd_{secrets.token_hex(16)}"
        device = Device(
            id=uuid.uuid4(),
            physical_id=physical_id,
            owner_id=owner.id,
            name="Test Edge Planter",
            status="active",
            token_hash=hash_opaque_token(raw_token),
        )
        with session_factory() as session:
            session.add(device)
            session.commit()
            session.refresh(device)
        return device, raw_token

    return _add


@pytest.fixture
def add_plant(session_factory: sessionmaker[Session]) -> Callable[..., Plant]:
    """Seed a plant for a user."""

    def _add(
        owner: User,
        *,
        name: str = "Monty",
        species_code: str | None = "monstera_deliciosa",
        device_id: str | None = None,
        level: int = 2,
        xp_ratio: float = 0.45,
    ) -> Plant:
        plant = Plant(
            id=uuid.uuid4(),
            owner_id=owner.id,
            name=name,
            species_code=species_code,
            device_id=device_id,
            level=level,
            xp_ratio=xp_ratio,
        )
        with session_factory() as session:
            session.add(plant)
            session.commit()
            session.refresh(plant)
        return plant

    return _add


@pytest.fixture
def user_token() -> Callable[[User], str]:
    """Generate JWT authorization token for a user."""

    def _make(user: User) -> str:
        return create_access_token(str(user.id))

    return _make
