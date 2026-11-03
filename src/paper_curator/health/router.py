import asyncio
import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel

from paper_curator.health.checks import CHECK_TIMEOUT_SECONDS, Check, Checks, get_checks

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/health", tags=["health"])

DependencyStatus = Literal["ok", "down"]


class Liveness(BaseModel):
    status: Literal["ok"] = "ok"


class Dependencies(BaseModel):
    postgres: DependencyStatus
    opensearch: DependencyStatus
    ollama: DependencyStatus


class Health(BaseModel):
    status: Literal["ok", "degraded", "unhealthy"]
    dependencies: Dependencies


async def probe(name: str, check: Check) -> DependencyStatus:
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
            return "ok" if await check() else "down"
    except Exception as error:
        logger.warning("Health check failed for %s: %s", name, type(error).__name__)
        return "down"


@router.get("/live")
async def live() -> Liveness:
    return Liveness()


@router.get("", responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": Health}})
async def health(response: Response, checks: Annotated[Checks, Depends(get_checks)]) -> Health:
    postgres, opensearch, ollama = await asyncio.gather(
        probe("postgres", checks.postgres),
        probe("opensearch", checks.opensearch),
        probe("ollama", checks.ollama),
    )
    dependencies = Dependencies(postgres=postgres, opensearch=opensearch, ollama=ollama)

    if "down" in (postgres, opensearch):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return Health(status="unhealthy", dependencies=dependencies)
    if ollama == "down":
        return Health(status="degraded", dependencies=dependencies)
    return Health(status="ok", dependencies=dependencies)
