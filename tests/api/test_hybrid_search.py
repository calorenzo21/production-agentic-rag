"""
The Search, through the API, against a real PostgreSQL and OpenSearch, both disposable, with Jina AI faked at its HTTP
edge. The Papers it finds are stored as the Ingestion stores them, then indexed with the indexing module, as the DAG
indexes them.
"""

from httpx import AsyncClient
from opensearchpy import OpenSearch
from src.config import Settings
from src.db.interfaces.base import BaseDatabase
from src.schemas.arxiv.paper import PaperCreate
from src.services.paper_indexer import make_paper_indexer

from tests.papers import chunk_index, content, hold, paper, words

SEARCH = "/api/v1/hybrid-search/"
# What the API answers when OpenSearch fails a Search, whatever the failure, so that its text never reaches the caller.
UNAVAILABLE = {"detail": "Search service is currently unavailable"}


async def hold_and_index(database: BaseDatabase, *papers: PaperCreate) -> None:
    """Store the Papers as the Ingestion does, then index them into the Index, which the API created at startup."""
    hold(database, *papers)
    await make_paper_indexer(database, Settings()).index_papers([held.arxiv_id for held in papers])


async def test_an_authors_name_finds_their_paper_whose_authors_come_back_as_a_list(
    client: AsyncClient, database: BaseDatabase
) -> None:
    await hold_and_index(
        database,
        paper("2411.00001", authors=["Ada Lovelace", "Charles Babbage"]),
        paper("2411.00002", authors=["Alan Turing"]),
    )

    response = await client.post(SEARCH, json={"query": "Lovelace"})

    [hit] = response.json()["hits"]
    assert (hit["arxiv_id"], hit["authors"]) == ("2411.00001", ["Ada Lovelace", "Charles Babbage"])


async def test_each_hit_has_its_papers_pdf_link_and_its_chunks_section_name(client: AsyncClient, database: BaseDatabase) -> None:
    two_sections = content(("Method", words(300, about="method")), ("Results", words(300, about="outcome")))
    await hold_and_index(
        database,
        paper("2411.00001", version=2, **two_sections),
        paper("2411.00002", **content(("Evaluation", words(300, about="outcome")))),
    )

    # A word of the Chunks made from the Results and Evaluation sections, and of no other.
    response = await client.post(SEARCH, json={"query": "outcome7"})

    found = {(hit["arxiv_id"], hit["pdf_url"], hit["section_name"]) for hit in response.json()["hits"]}
    assert found == {
        ("2411.00001", "https://arxiv.org/pdf/2411.00001v2", "Results"),
        ("2411.00002", "https://arxiv.org/pdf/2411.00002v1", "Evaluation"),
    }


async def test_a_search_answers_503_without_the_exceptions_text_when_the_index_is_missing(
    client: AsyncClient, opensearch: OpenSearch
) -> None:
    # OpenSearch still answers, and its cluster is healthy, but it fails any Search of an index that does not exist.
    opensearch.indices.delete(index=chunk_index())

    response = await client.post(SEARCH, json={"query": "attention"})

    assert (response.status_code, response.json()) == (503, UNAVAILABLE)


async def test_a_search_answers_503_without_the_exceptions_text_when_opensearch_does_not_answer(
    opensearch_down: None, client: AsyncClient
) -> None:
    response = await client.post(SEARCH, json={"query": "attention"})

    assert (response.status_code, response.json()) == (503, UNAVAILABLE)
