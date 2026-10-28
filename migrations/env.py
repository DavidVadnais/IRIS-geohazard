from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, text

from geohazard.config.settings import get_settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

settings = get_settings()


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database.sqlalchemy_url(),
        literal_binds=True,
        version_table_schema=settings.staging_schema,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(settings.database.sqlalchemy_url())
    with engine.connect() as connection:
        connection.execute(
            text(f"CREATE SCHEMA IF NOT EXISTS {settings.staging_schema}")
        )
        connection.commit()
        context.configure(
            connection=connection, version_table_schema=settings.staging_schema
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
