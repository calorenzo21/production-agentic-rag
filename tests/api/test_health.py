"""
The health check, through the API, against a real PostgreSQL and OpenSearch, both disposable, with Jina AI faked at its
HTTP edge. The Papers it counts are stored as the Ingestion stores them, then indexed with the indexing module, as the
DAG indexes them, or else, when there are too many to index, their Chunks are written straight into the Index.
"""

import asyncio

from httpx import AsyncClient
from opensearchpy import OpenSearch, helpers
from src.db.interfaces.base import BaseDatabase

from tests.api.hanging_opensearch import HangingOpenSearch
from tests.papers import chunk_index, content, hold_and_index, paper, words

HEALTH = "/api/v1/health"
PING = "/api/v1/ping"


async def test_the_health_check_reports_the_papers_and_the_chunks_the_index_holds(
    client: AsyncClient, database: BaseDatabase
) -> None:
    await hold_and_index(
        database,
        # Two Chunks, one per section.
        paper("2411.00001", **content(("Method", words(300, about="method")), ("Results", words(300, about="result")))),
        # One Chunk, its title and abstract, as it has no Content.
        paper("2411.00002"),
    )

    response = await client.get(HEALTH)

    assert response.json()["services"]["opensearch"] == {
        "status": "healthy",
        "message": f"Index '{chunk_index()}' with 2 Papers and 3 Chunks",
    }


async def test_the_health_check_counts_every_paper_however_many_the_index_holds(
    client: AsyncClient, opensearch: OpenSearch
) -> None:
    index = chunk_index()
    # More Papers than OpenSearch counts the distinct values of exactly: above its precision threshold, 3,000 by default
    # and 40,000 at most, it counts them only approximately.
    arxiv_ids = [f"2411.{number:05d}" for number in range(40_001)]
    # Each Paper's first Chunk, and the first Paper's second, written straight into the Index, as indexing so many Papers
    # would take too long, each with no more than its Paper's arXiv ID.
    chunks = [(f"{arxiv_id}_0", arxiv_id) for arxiv_id in arxiv_ids] + [(f"{arxiv_ids[0]}_1", arxiv_ids[0])]
    helpers.bulk(
        opensearch, ({"_index": index, "_id": chunk_id, "_source": {"arxiv_id": arxiv_id}} for chunk_id, arxiv_id in chunks)
    )
    opensearch.indices.refresh(index=index)

    response = await client.get(HEALTH)

    assert response.json()["services"]["opensearch"]["message"] == f"Index '{index}' with 40001 Papers and 40002 Chunks"


async def test_the_health_check_reports_opensearch_unhealthy_when_the_index_is_missing(
    client: AsyncClient, opensearch: OpenSearch
) -> None:
    # OpenSearch still answers, and its cluster is healthy, but it cannot count the Papers of an index that does not exist.
    opensearch.indices.delete(index=chunk_index())

    response = await client.get(HEALTH)

    assert response.json()["services"]["opensearch"]["status"] == "unhealthy"


async def test_the_api_answers_another_request_while_the_health_check_waits_for_an_opensearch_that_does_not_answer(
    hanging_opensearch: HangingOpenSearch, client: AsyncClient
) -> None:
    async with hanging_opensearch.hanging() as asked:
        health = asyncio.create_task(client.get(HEALTH))
        # Well before opensearch-py's timeout, 10 seconds, which a health check that stalls the API would wait out first.
        async with asyncio.timeout(5):
            await asked.wait()
            ping = await client.get(PING)

        assert (ping.status_code, health.done()) == (200, False)
    # OpenSearch then drops the health check's connection, so that the health check answers, and ends with the test.
    await health
