"""001 init extensions and schemas

Revision ID: 001_init_ext_schemas
Revises:
Create Date: 2026-10-09 19:40:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '001_init_ext_schemas'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMAS = [
    "auth",
    "registry",
    "knowledge",
    "action",
    "orchestrator",
    "assessment",
    "advice",
    "companion",
]


def upgrade() -> None:
    # 1. Enable pgvector extension
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector;"))

    # 2. Create bounded context schemas
    for schema in SCHEMAS:
        op.execute(sa.text(f'CREATE SCHEMA IF NOT EXISTS "{schema}";'))

    # 3. Create initial tables for all bounded contexts
    bind = op.get_bind()
    import action.db
    import action.models  # noqa: F401
    import advice.db
    import advice.models
    import assessment.db
    import assessment.models  # noqa: F401
    import companion.db
    import companion.models  # noqa: F401
    import core.db
    import core.devices.models  # noqa: F401
    import core.users.models  # noqa: F401
    import knowledge.db
    import knowledge.models  # noqa: F401
    import orchestrator.db
    import orchestrator.models  # noqa: F401
    import registry.db
    import registry.models  # noqa: F401

    for meta in [
        core.db.Base.metadata,
        registry.db.Base.metadata,
        knowledge.db.Base.metadata,
        action.db.Base.metadata,
        orchestrator.db.Base.metadata,
        assessment.db.Base.metadata,
        companion.db.Base.metadata,
    ]:
        meta.create_all(bind=bind)

    advice.models.CarePlan.__table__.create(bind=bind, checkfirst=True)
    advice.models.Diagnosis.__table__.create(bind=bind, checkfirst=True)

    # 4. Create knowledge freshness view
    knowledge.db.create_views(bind)


def downgrade() -> None:
    for schema in reversed(SCHEMAS):
        op.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE;'))
    op.execute(sa.text("DROP EXTENSION IF EXISTS vector;"))
