from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from src.loopie.config import get_settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _sqlalchemy_url() -> str:
    # The app's runtime pool connects with plain psycopg (v3) directly, which
    # accepts a bare `postgresql://` URL. SQLAlchemy's engine, used only here
    # for Alembic, defaults that same scheme to the psycopg2 dialect, which
    # this project does not depend on. Force the psycopg3 dialect so the
    # online migration path uses the one driver already installed.
    url = get_settings().postgres_url
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return url.replace("%", "%%")


config.set_main_option("sqlalchemy.url", _sqlalchemy_url())
target_metadata = None


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table="alembic_version",
        version_table_schema="loopie",
        include_schemas=True,
    )
    context.execute("CREATE SCHEMA IF NOT EXISTS loopie")
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        connection.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS loopie")
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table="alembic_version",
            version_table_schema="loopie",
            include_schemas=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
