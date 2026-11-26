"""Test configuration and shared fixtures."""

import socket
from collections.abc import Iterator
from functools import partial
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
from src.services import paper_indexer
from src.services.embeddings.factory import make_embeddings_service
from src.services.opensearch.factory import make_opensearch_client
from testcontainers.opensearch import OpenSearchContainer
from testcontainers.postgres import PostgresContainer

from tests.fake_jina import FakeJina

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
    """
    A disposable OpenSearch at the stack's version, with its security plugin disabled, so reached over plain HTTP, also
    through the application's settings.
    """
    container = OpenSearchContainer("opensearchproject/opensearch:2.19.0")
    # Query Insights would write the tests' queries to a top_queries-* index in the background, even between tests.
    for metric in ("latency", "cpu", "memory"):
        container.with_env(f"search.insights.top_queries.{metric}.enabled", "false")
    with container, pytest.MonkeyPatch.context() as environment:
        config = container.get_config()
        environment.setenv("OPENSEARCH__HOST", f"http://{config['host']}:{config['port']}")
        # Its own login, which it ignores without security, so that a rag_app password in .env never reaches it.
        environment.setenv("OPENSEARCH__USERNAME", config["username"])
        environment.setenv("OPENSEARCH__PASSWORD", config["password"])
        get_settings.cache_clear()
        make_opensearch_client.cache_clear()
        yield container.get_client()
    get_settings.cache_clear()
    make_opensearch_client.cache_clear()


@pytest.fixture
def opensearch(disposable_opensearch: OpenSearch) -> Iterator[OpenSearch]:
    """The disposable OpenSearch, with no indices or search pipelines left after each test."""
    yield disposable_opensearch
    # Except those whose names start with a dot, where OpenSearch's plugins keep their configuration.
    disposable_opensearch.indices.delete(index="*,-.*")
    # The cluster holds them apart from any index, so they outlive the indices, such as the RRF pipeline.
    disposable_opensearch.search_pipeline.delete(id="*")


@pytest.fixture
def opensearch_down(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """
    The settings point at a port nothing listens on, as if OpenSearch were down. The API reads them, and builds its
    OpenSearch client, once per process, so both are read again at its next startup, and again after the test.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        host, port = probe.getsockname()
    # Free again, so that the port refuses every connection at once. On macOS, a port still bound times connections out.
    monkeypatch.setenv("OPENSEARCH__HOST", f"http://{host}:{port}")
    get_settings.cache_clear()
    make_opensearch_client.cache_clear()
    yield
    get_settings.cache_clear()
    make_opensearch_client.cache_clear()


@pytest.fixture
def jina() -> FakeJina:
    """Jina AI, faked at its HTTP edge, which a Jina client reaches through the fake's transport."""
    return FakeJina()


@pytest.fixture(autouse=True)
def indexing_reaching_the_fake_jina(jina: FakeJina, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    The indexing module builds its Jina client with the factory, which takes the transport the client reaches Jina
    through. In every test, it passes the fake Jina's, so that no test that indexes Papers reaches the real Jina.
    """
    monkeypatch.setattr(paper_indexer, "make_embeddings_service", partial(make_embeddings_service, transport=jina.transport))
