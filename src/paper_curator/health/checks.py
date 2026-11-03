import ssl
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from paper_curator.settings import Settings

Check = Callable[[], Awaitable[bool]]

CHECK_TIMEOUT_SECONDS = 3.0


@dataclass(frozen=True)
class Checks:
    postgres: Check
    opensearch: Check
    ollama: Check


def get_checks(request: Request) -> Checks:
    checks: Checks = request.app.state.checks
    return checks


@asynccontextmanager
async def open_checks(settings: Settings) -> AsyncIterator[Checks]:
    """Yield checks that really reach each dependency, holding their connections open."""
    engine = create_async_engine(settings.postgres.url, pool_pre_ping=True, pool_size=1)
    opensearch_ca = settings.opensearch.ca_cert_path
    opensearch = httpx.AsyncClient(
        base_url=settings.opensearch.url,
        auth=(
            settings.opensearch.username,
            settings.opensearch.password.get_secret_value(),
        ),
        verify=ssl.create_default_context(cafile=opensearch_ca) if opensearch_ca else True,
        timeout=CHECK_TIMEOUT_SECONDS,
    )
    ollama = httpx.AsyncClient(base_url=settings.ollama.url, timeout=CHECK_TIMEOUT_SECONDS)

    async def check_postgres() -> bool:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return True

    async def check_opensearch() -> bool:
        response = await opensearch.get("/_cluster/health")
        return response.is_success and response.json()["status"] in {"green", "yellow"}

    async def check_ollama() -> bool:
        return (await ollama.get("/api/version")).is_success

    try:
        yield Checks(postgres=check_postgres, opensearch=check_opensearch, ollama=check_ollama)
    finally:
        await ollama.aclose()
        await opensearch.aclose()
        await engine.dispose()
