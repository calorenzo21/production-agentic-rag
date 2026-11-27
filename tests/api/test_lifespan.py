"""The API starting and stopping, against a real PostgreSQL and OpenSearch, both disposable, with Jina faked."""

from asgi_lifespan import LifespanManager
from opensearchpy import OpenSearch
from src.db.interfaces.base import BaseDatabase
from src.main import app

from tests.fake_jina import FakeJina


async def test_the_api_keeps_its_jina_client_open_while_it_runs_and_closes_it_when_it_stops(
    database: BaseDatabase, opensearch: OpenSearch, jina: FakeJina
) -> None:
    async with LifespanManager(app):
        assert not jina.closed

    assert jina.closed
