"""API test configuration and fixtures."""

from functools import partial

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from opensearchpy import OpenSearch
from src import main
from src.config import get_settings
from src.db.interfaces.base import BaseDatabase
from src.services.embeddings.factory import make_embeddings_service

from tests.api.hanging_opensearch import HangingOpenSearch
from tests.fake_jina import FakeJina


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    """Async backend for testing."""
    return "asyncio"


@pytest.fixture(autouse=True)
def api_reaching_the_fake_jina(jina: FakeJina, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    The API builds its Jina client at startup with the factory, which takes the transport the client reaches Jina
    through. In every API test, it passes the fake Jina's, so that no API test reaches the real Jina.
    """
    monkeypatch.setattr(main, "make_embeddings_service", partial(make_embeddings_service, transport=jina.transport))


@pytest.fixture
def hanging_opensearch(opensearch_down: None) -> HangingOpenSearch:
    """
    OpenSearch, which the test makes hang. Until then it is down, as opensearch_down makes it, so that the API starts at
    once, where a hanging OpenSearch would keep its startup waiting for opensearch-py's timeout, 10 seconds.
    """
    return HangingOpenSearch(get_settings().opensearch.host)


@pytest.fixture
async def client(database: BaseDatabase, opensearch: OpenSearch):
    """
    HTTP client for API testing. The API connects at startup, and creates the Index, so it starts once the disposable
    PostgreSQL and OpenSearch are set up. It reaches the fake Jina.
    """
    async with (
        LifespanManager(main.app) as manager,
        AsyncClient(transport=ASGITransport(app=manager.app), base_url="http://test") as client,
    ):
        yield client
