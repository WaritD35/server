"""``vision`` is registered with Alembic and created by the web lifespan."""

import ast
import asyncio
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import capture.db
import main_web

ENV_PY = Path(__file__).resolve().parents[2] / "alembic" / "env.py"


def _env_tree() -> ast.Module:
    return ast.parse(ENV_PY.read_text())


def _assigned(tree: ast.Module, name: str) -> ast.expr:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return node.value
    raise AssertionError(f"{name} is not assigned in alembic/env.py")


def test_env_imports_capture_models() -> None:
    imported = {
        alias.name
        for node in _env_tree().body
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert {"capture.db", "capture.models"} <= imported


def test_vision_is_a_managed_schema() -> None:
    assert "vision" in ast.literal_eval(_assigned(_env_tree(), "MANAGED_SCHEMAS"))


def test_capture_metadata_is_a_migration_target() -> None:
    value = _assigned(_env_tree(), "target_metadata")
    assert isinstance(value, ast.List)
    assert "capture.db.Base.metadata" in {ast.unparse(e) for e in value.elts}


def test_lifespan_initialises_the_vision_schema() -> None:
    assert main_web.init_capture_models is capture.db.init_models
    init_names = [name for name in vars(main_web) if name.startswith("init_")]
    with ExitStack() as stack:
        mocks = {name: stack.enter_context(patch.object(main_web, name)) for name in init_names}
        for name in ("ensure_admin_user", "SessionLocal", "OrchestratorLoop"):
            stack.enter_context(patch.object(main_web, name))

        async def _run() -> None:
            async with main_web.lifespan(main_web.app):
                pass

        asyncio.run(_run())

    mocks["init_capture_models"].assert_called_once_with()
