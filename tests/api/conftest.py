"""API test configuration and fixtures."""

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from src.db.interfaces.base import BaseDatabase
from src.main import app


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    """Async backend for testing."""
    return "asyncio"


@pytest.fixture
async def client(database: BaseDatabase):
    """HTTP client for API testing. The API connects at startup, so it starts once the disposable PostgreSQL is set up."""
    async with (
        LifespanManager(app) as manager,
        AsyncClient(transport=ASGITransport(app=manager.app), base_url="http://test") as client,
    ):
        yield client
