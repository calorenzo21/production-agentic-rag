"""
The Search, through the API, against a real PostgreSQL and OpenSearch, both disposable, with Jina AI faked at its HTTP
edge. The Papers it finds are stored as the Ingestion stores them, then indexed with the indexing module, as the DAG
indexes them.
"""

from collections.abc import Iterator

import pytest
from httpx import AsyncClient
from opensearchpy import OpenSearch
from src.config import Settings, get_settings
from src.db.interfaces.base import BaseDatabase
from src.schemas.arxiv.paper import PaperCreate
from src.services.opensearch.factory import make_opensearch_client
from src.services.paper_indexer import make_paper_indexer

from tests.fake_jina import Failure, FakeJina, halfway, own_vector
from tests.papers import chunk_index, content, hold, paper, words

SEARCH = "/api/v1/hybrid-search/"
# What the API answers when OpenSearch fails a Search, whatever the failure, so that its text never reaches the caller.
UNAVAILABLE = {"detail": "Search service is currently unavailable"}
# A meaning a test makes Jina embed a text as: a unit vector of jina-embeddings-v3's 1024 dimensions, as Jina's are.
A_MEANING = own_vector("a meaning", 1024)
# A meaning near it, though less near than itself: halfway to an unrelated meaning.
NEAR_A_MEANING = halfway(A_MEANING, own_vector("an unrelated meaning", 1024))
# A Search by words alone. One by words and meaning also returns the Chunks nearest the query's meaning, however far, so
# every Chunk of an Index this small.
BY_WORDS_ALONE = {"use_hybrid": False}


async def hold_and_index(database: BaseDatabase, *papers: PaperCreate) -> None:
    """Store the Papers as the Ingestion does, then index them into the Index, which the API created at startup."""
    hold(database, *papers)
    await make_paper_indexer(database, Settings()).index_papers([held.arxiv_id for held in papers])


@pytest.fixture
def rrf_pipeline_named_otherwise(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """
    The settings name the RRF pipeline otherwise than the course does. The API reads them, and builds its OpenSearch
    client, once per process, so both are read again at its next startup, and again after the test.
    """
    name = "arxiv-papers-fusion"
    monkeypatch.setenv("OPENSEARCH__RRF_PIPELINE_NAME", name)
    get_settings.cache_clear()
    make_opensearch_client.cache_clear()
    yield name
    get_settings.cache_clear()
    make_opensearch_client.cache_clear()


async def test_a_paper_is_found_by_the_meaning_of_a_query_that_shares_no_word_with_it(
    client: AsyncClient, database: BaseDatabase, jina: FakeJina
) -> None:
    query = "turning one language into another"
    jina.embeds("Neural machine translation", as_vector=A_MEANING)
    jina.embeds(query, as_vector=A_MEANING)
    await hold_and_index(
        database,
        paper("2411.00001", title="Neural machine translation with attention", abstract="We translate sentences."),
        paper("2411.00002"),
        paper("2411.00003"),
    )

    by_words = await client.post(SEARCH, json={"query": query, **BY_WORDS_ALONE})
    response = await client.post(SEARCH, json={"query": query})

    assert by_words.json()["hits"] == []
    found = response.json()
    # The other Papers come back after it, as the nearest Chunks to the query's meaning, however far.
    assert (found["search_mode"], [hit["arxiv_id"] for hit in found["hits"]][:1]) == ("hybrid", ["2411.00001"])


async def test_the_category_filter_holds_whether_a_paper_is_found_by_its_words_or_by_its_meaning(
    client: AsyncClient, database: BaseDatabase, jina: FakeJina
) -> None:
    query = "papers on speech recognition"
    jina.embeds("Turning audio into text", as_vector=A_MEANING)
    jina.embeds(query, as_vector=A_MEANING)
    await hold_and_index(
        database,
        paper("2411.00001", title="Speech recognition for children", categories=["cs.AI"]),
        # Found by its words, as the first.
        paper("2411.00002", title="Speech recognition in noise", categories=["cs.LG"]),
        # Found by its meaning alone.
        paper("2411.00003", title="Turning audio into text", categories=["cs.LG"]),
    )

    response = await client.post(SEARCH, json={"query": query, "categories": ["cs.AI"]})

    found = response.json()
    assert (found["search_mode"], [hit["arxiv_id"] for hit in found["hits"]]) == ("hybrid", ["2411.00001"])


async def test_a_paper_of_the_category_is_found_by_meaning_however_many_nearer_chunks_other_categories_hold(
    client: AsyncClient, database: BaseDatabase, jina: FakeJina
) -> None:
    query = "turning sound into words"
    jina.embeds(query, as_vector=A_MEANING)
    jina.embeds("Acoustic models", as_vector=NEAR_A_MEANING)
    jina.embeds("Speech recognition", as_vector=A_MEANING)
    await hold_and_index(
        database,
        paper("2411.00001", title="Acoustic models for children", categories=["cs.AI"]),
        paper("2411.00002", title="Speech recognition in noise", categories=["cs.LG"]),
        paper("2411.00003", title="Speech recognition at scale", categories=["cs.LG"]),
    )

    # The vector half looks for the nearest Chunks, twice as many as the page holds: two, which the other Papers' would
    # fill if the filter applied after the vector search rather than inside it.
    response = await client.post(SEARCH, json={"query": query, "categories": ["cs.AI"], "size": 1})

    found = response.json()
    assert (found["search_mode"], [hit["arxiv_id"] for hit in found["hits"]]) == ("hybrid", ["2411.00001"])


async def test_a_search_by_meaning_goes_through_the_rrf_pipeline_the_settings_name(
    rrf_pipeline_named_otherwise: str, client: AsyncClient, database: BaseDatabase, opensearch: OpenSearch
) -> None:
    await hold_and_index(database, paper("2411.00001"))

    response = await client.post(SEARCH, json={"query": "attention"})

    # The API created it at startup under that name alone, and the Search went through it there.
    pipelines = list(opensearch.search_pipeline.get())
    assert (response.json().get("search_mode"), pipelines) == ("hybrid", [rrf_pipeline_named_otherwise])


@pytest.mark.parametrize("failure", list(Failure), ids=lambda failure: failure.value)
async def test_when_jina_fails_a_search_falls_back_at_once_to_words_alone_and_says_so(
    failure: Failure, client: AsyncClient, database: BaseDatabase, jina: FakeJina
) -> None:
    await hold_and_index(database, paper("2411.00001", title="Speech recognition for children"), paper("2411.00002"))
    jina.keeps_failing(failure)

    response = await client.post(SEARCH, json={"query": "speech recognition"})

    assert response.status_code == 200, response.text
    found = response.json()
    assert (found["search_mode"], [hit["arxiv_id"] for hit in found["hits"]]) == ("bm25", ["2411.00001"])
    # Jina was asked to embed the query once, as a Search retries nothing, not even a rate limit, which the indexing does.
    assert [request["task"] for request in jina.requests].count("retrieval.query") == 1


async def test_an_authors_name_finds_their_paper_whose_authors_come_back_as_a_list(
    client: AsyncClient, database: BaseDatabase
) -> None:
    await hold_and_index(
        database,
        paper("2411.00001", authors=["Ada Lovelace", "Charles Babbage"]),
        paper("2411.00002", authors=["Alan Turing"]),
    )

    response = await client.post(SEARCH, json={"query": "Lovelace", **BY_WORDS_ALONE})

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
    response = await client.post(SEARCH, json={"query": "outcome7", **BY_WORDS_ALONE})

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
