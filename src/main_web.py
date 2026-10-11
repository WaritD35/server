"""Web process entrypoint — the FastAPI application.

Run with::

    uv run uvicorn main_web:app --app-dir src --reload

There is no central HTTP layer: each bounded context owns its HTTP surface in
``<context>/api.py`` (exposing ``router: APIRouter``) together with its own
request/response schemas. ``core`` provides the health endpoint. Register each
context's router below as it lands.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from action import router as action_router
from action.db import init_models as init_action_models
from advice.db import init_models as init_advice_models
from api.controller import router as companion_router
from api.exception import ApiException
from api.model import BaseResponse
from assessment.db import init_models as init_assessment_models
from companion.db import init_models as init_companion_models
from core.api import router as health_router
from core.db import SessionLocal, init_models
from core.devices import router as devices_router
from core.users import ensure_admin_user
from core.users import router as auth_router
from knowledge import router as knowledge_router
from knowledge.db import init_models as init_knowledge_models
from orchestrator import OrchestratorLoop
from orchestrator import router as orchestrator_router
from orchestrator.db import init_models as init_orchestrator_models
from registry import router as registry_router
from registry.db import init_models as init_registry_models


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Interim: create schema tables on startup until per-schema
    # Alembic migrations land across all deployments.
    init_models()
    init_knowledge_models()
    init_registry_models()
    init_orchestrator_models()
    init_action_models()
    init_assessment_models()
    init_advice_models()
    init_companion_models()
    # Seed / reconcile the bootstrap admin from ADMIN_* settings.
    with SessionLocal() as session:
        ensure_admin_user(session)
    # The scheduled pipeline. Safe to run in every web process: tasks are
    # claimed atomically, so each is executed once.
    loop = OrchestratorLoop(SessionLocal)
    loop.start()
    try:
        yield
    finally:
        loop.stop()


def create_app() -> FastAPI:
    openapi_tags = [
        {
            "name": "Companion",
            "description": (
                "Character dialogue, gamification status, plant voice, and care status "
                "for web clients and edge devices."
            ),
        }
    ]

    from core.config import get_settings

    settings = get_settings()
    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]

    app = FastAPI(
        title="Pet-Plant API",
        version="0.1.0",
        summary="Cloud backend for Pet-Plant.",
        lifespan=lifespan,
        openapi_tags=openapi_tags,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(ApiException)
    async def api_exception_handler(_request: Request, exc: ApiException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=BaseResponse.fail(
                message=exc.message,
                error_code=exc.error_code,
                details=exc.details,
            ).model_dump(mode="json"),
        )

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(devices_router)
    app.include_router(knowledge_router)
    app.include_router(registry_router)
    app.include_router(orchestrator_router)
    app.include_router(action_router)
    app.include_router(companion_router)

    return app


app = create_app()
