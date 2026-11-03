from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from paper_curator.health.checks import open_checks
from paper_curator.health.router import router as health_router
from paper_curator.logs import configure_logging
from paper_curator.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings if settings is not None else Settings()
    configure_logging(resolved.log)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with open_checks(resolved) as checks:
            app.state.checks = checks
            yield

    app = FastAPI(title="arXiv Paper Curator", lifespan=lifespan)
    app.include_router(health_router)
    return app
