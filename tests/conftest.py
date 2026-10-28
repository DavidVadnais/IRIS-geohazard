import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from geohazard.config.settings import get_settings

TEST_DB_SUFFIX = "_test"

STAGING_TABLES = (
    "iris_staging.mining_restrictions_rejected",
    "iris_staging.mining_restrictions",
    "iris_staging.harvest_runs",
)


def _use_test_database() -> str:
    main_settings = get_settings()
    test_name = main_settings.database.name + TEST_DB_SUFFIX
    admin = create_engine(main_settings.database.sqlalchemy_url(), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            exists = connection.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": test_name}).scalar()
            if not exists:
                connection.execute(text(f'CREATE DATABASE "{test_name}"'))
    finally:
        admin.dispose()
    os.environ["IRIS_DATABASE__NAME"] = test_name
    get_settings.cache_clear()
    return get_settings().database.sqlalchemy_url()


@pytest.fixture(scope="session")
def migrated_engine():
    try:
        url = _use_test_database()
    except OperationalError as exc:
        pytest.skip(f"PostGIS not reachable, skipping db tests: {exc.orig}")
    engine = create_engine(url)
    command.upgrade(Config("alembic.ini"), "head")
    yield engine
    engine.dispose()


@pytest.fixture
def engine(migrated_engine):
    with migrated_engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {', '.join(STAGING_TABLES)} RESTART IDENTITY CASCADE"))
    return migrated_engine
