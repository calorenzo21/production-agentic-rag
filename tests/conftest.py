"""Test configuration and shared fixtures."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from src.config import get_settings
from src.db.factory import make_database
from src.db.interfaces.base import BaseDatabase
from src.models.base import Base
from testcontainers.postgres import PostgresContainer

REPOSITORY = Path(__file__).parents[1]


@pytest.fixture(scope="session")
def migrated_database() -> Iterator[BaseDatabase]:
    """A disposable PostgreSQL, with the schema the Alembic migrations build, reached through the application's settings."""
    with PostgresContainer("postgres:16-alpine") as postgres, pytest.MonkeyPatch.context() as environment:
        environment.setenv("POSTGRES_DATABASE_URL", postgres.get_connection_url())
        get_settings.cache_clear()
        command.upgrade(Config(REPOSITORY / "alembic.ini"), "head")
        database = make_database()
        yield database
        database.teardown()
    get_settings.cache_clear()


@pytest.fixture
def database(migrated_database: BaseDatabase) -> Iterator[BaseDatabase]:
    """The disposable PostgreSQL, emptied after each test."""
    yield migrated_database
    with migrated_database.get_session() as session:
        session.execute(text(f"TRUNCATE {', '.join(table.name for table in Base.metadata.sorted_tables)}"))
        session.commit()
