"""Test configuration and shared fixtures."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from opensearchpy import OpenSearch
from sqlalchemy import text
from src.config import get_settings
from src.db.factory import make_database
from src.db.interfaces.base import BaseDatabase
from src.models.base import Base
from testcontainers.opensearch import OpenSearchContainer
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


@pytest.fixture(scope="session")
def disposable_opensearch() -> Iterator[OpenSearch]:
    """A disposable OpenSearch at the stack's version, with its security plugin disabled, so reached over plain HTTP."""
    container = OpenSearchContainer("opensearchproject/opensearch:2.19.0")
    # Query Insights would write the tests' queries to a top_queries-* index in the background, even between tests.
    for metric in ("latency", "cpu", "memory"):
        container.with_env(f"search.insights.top_queries.{metric}.enabled", "false")
    with container:
        yield container.get_client()


@pytest.fixture
def opensearch(disposable_opensearch: OpenSearch) -> Iterator[OpenSearch]:
    """The disposable OpenSearch, with no indices left after each test."""
    yield disposable_opensearch
    # Except those whose names start with a dot, where OpenSearch's plugins keep their configuration.
    disposable_opensearch.indices.delete(index="*,-.*")
