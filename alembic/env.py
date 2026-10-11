import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Add 'src' to python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

import action.db
import action.models  # noqa: F401
import advice.db
import advice.models  # noqa: F401
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
from core.config import get_settings

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Set database URL from core settings if available
try:
    settings = get_settings()
    config.set_main_option("sqlalchemy.url", settings.database_url)
except Exception:
    pass

# Collect metadata from all contexts
target_metadata = [
    core.db.Base.metadata,
    registry.db.Base.metadata,
    knowledge.db.Base.metadata,
    action.db.Base.metadata,
    orchestrator.db.Base.metadata,
    assessment.db.Base.metadata,
    advice.db.Base.metadata,
    companion.db.Base.metadata,
]

# Track which schemas we manage
MANAGED_SCHEMAS = {
    "auth",
    "registry",
    "knowledge",
    "action",
    "orchestrator",
    "assessment",
    "advice",
    "companion",
}


def include_object(object, name, type_, reflected, compare_to):
    if type_ == "schema":
        return name in MANAGED_SCHEMAS
    return True


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            include_object=include_object,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
