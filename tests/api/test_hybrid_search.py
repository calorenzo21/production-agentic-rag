"""
The Search, through the API, against a real PostgreSQL and OpenSearch, both disposable, with Jina AI faked at its HTTP
edge. The Papers it finds are stored as the Ingestion stores them, then indexed with the indexing module, as the DAG
indexes them.
"""

import asyncio
from collections.abc import Callable, Iterator
from datetime import datetime

import pytest
from httpx import AsyncClient
from opensearchpy import OpenSearch
from src.config import get_settings
from src.db.interfaces.base import BaseDatabase
from src.services.opensearch.factory import make_opensearch_client

from tests.api.hanging_opensearch import HangingOpenSearch
from tests.fake_jina import Failure, FakeJina, halfway, own_vector
from tests.papers import chunk_index, content, hold_and_index, paper, words

SEARCH = "/api/v1/hybrid-search/"
PING = "/api/v1/ping"
# What the API answers when OpenSearch fails a Search, whatever the failure, so that its text never reaches the caller.
UNAVAILABLE = {"detail": "Search service is currently unavailable"}
# A meaning a test makes Jina embed a text as: a unit vector of jina-embeddings-v3's 1024 dimensions, as Jina's are.
A_MEANING = own_vector("a meaning", 1024)
# A meaning near it, though less near than itself: halfway to an unrelated meaning.
NEAR_A_MEANING = halfway(A_MEANING, own_vector("an unrelated meaning", 1024))
# A Search by words alone. One by words and meaning also returns the Chunks nearest the query's meaning, however far, so
# every Chunk of an Index this small.
BY_WORDS_ALONE = {"use_hybrid": False}
# Each mode of a Search, with the search_mode it answers.
IN_BOTH_MODES = pytest.mark.parametrize(
    ("mode", "search_mode"),
    [({"use_hybrid": True}, "hybrid"), (BY_WORDS_ALONE, "bm25")],
    ids=["by words and meaning", "by words alone"],
)
# A query that every Chunk of the Papers hold_papers_matching_the_query holds matches by its words.
QUERY = "attention please"


async def hold_papers_matching_the_query(database: BaseDatabase, jina: FakeJina) -> None:
    """
    Store and index three Papers, all of whose Chunks match QUERY by its words, and each with one Chunk that matches it
    best: the first Paper's Results, which matches it best of all, by its words and by its meaning, then the second's
    Introduction, then the third's only Chunk, its Evaluation. The newest is the second, then the third, then the first.
    """
    jina.embeds(QUERY, as_vector=A_MEANING)
    # A word of the Results section, and of no other text.
    jina.embeds("result0 ", as_vector=A_MEANING)
    jina.embeds("introduction0 ", as_vector=NEAR_A_MEANING)
    await hold_and_index(
        database,
        paper(
            "2411.00001",
            published_date=datetime(2024, 11, 1),
            **content(
                ("Method", words(300, about="method") + " attention"),
                ("Results", words(300, about="result") + " attention attention attention"),
                ("Discussion", words(300, about="discussion") + " attention"),
            ),
        ),
        paper(
            "2411.00002",
            published_date=datetime(2024, 11, 3),
            **content(
                ("Introduction", words(300, about="introduction") + " attention attention"),
                ("Conclusion", words(300, about="conclusion") + " attention"),
            ),
        ),
        paper(
            "2411.00003",
            published_date=datetime(2024, 11, 2),
            **content(("Evaluation", words(300, about="evaluation") + " attention")),
        ),
    )


@pytest.fixture
def setting(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[str, str], None]]:
    """
    Give a setting a value through its variable, as .env does. The API reads the settings, and builds its OpenSearch
    client, once per process, so both are read again at its next startup, and again after the test.
    """

    def set_setting(variable: str, value: str) -> None:
        monkeypatch.setenv(variable, value)
        get_settings.cache_clear()
        make_opensearch_client.cache_clear()

    yield set_setting
    get_settings.cache_clear()
    make_opensearch_client.cache_clear()


@pytest.fixture
def rrf_pipeline_named_otherwise(setting: Callable[[str, str], None]) -> str:
    """The settings name the RRF pipeline otherwise than the course does."""
    name = "arxiv-papers-fusion"
    setting("OPENSEARCH__RRF_PIPELINE_NAME", name)
    return name


@pytest.fixture
def each_half_keeping_100_chunks(setting: Callable[[str, str], None]) -> None:
    """
    The settings multiply the largest page, 100, by 1 rather than by the course's 2, so that each half of a Search by
    words and meaning keeps 100 Chunks.
    """
    setting("OPENSEARCH__HYBRID_SEARCH_SIZE_MULTIPLIER", "1")


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
    each_half_keeping_100_chunks: None, client: AsyncClient, database: BaseDatabase, jina: FakeJina
) -> None:
    query = "turning sound into words"
    jina.embeds(query, as_vector=A_MEANING)
    jina.embeds("Acoustic models", as_vector=NEAR_A_MEANING)
    jina.embeds("Speech recognition", as_vector=A_MEANING)
    await hold_and_index(
        database,
        paper("2411.00001", title="Acoustic models for children", categories=["cs.AI"]),
        # About 120 Chunks, which all hold its title, so are all nearer the query's meaning. The vector half keeps the 100
        # nearest Chunks, which they would fill if the filter applied after the vector search rather than inside it.
        paper(
            "2411.00002",
            title="Speech recognition at scale",
            categories=["cs.LG"],
            **content(("Method", words(60_000, about="method"))),
        ),
    )

    response = await client.post(SEARCH, json={"query": query, "categories": ["cs.AI"]})

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


@IN_BOTH_MODES
async def test_each_paper_comes_back_once_with_its_best_chunk_and_the_total_counts_papers(
    mode: dict[str, bool], search_mode: str, client: AsyncClient, database: BaseDatabase, jina: FakeJina
) -> None:
    await hold_papers_matching_the_query(database, jina)

    response = await client.post(SEARCH, json={"query": QUERY, **mode})

    found = response.json()
    assert (found["search_mode"], found["total"], [(hit["arxiv_id"], hit["section_name"]) for hit in found["hits"]]) == (
        search_mode,
        3,
        [("2411.00001", "Results"), ("2411.00002", "Introduction"), ("2411.00003", "Evaluation")],
    )


@IN_BOTH_MODES
async def test_paging_a_search_returns_each_paper_once_across_its_pages(
    mode: dict[str, bool], search_mode: str, client: AsyncClient, database: BaseDatabase, jina: FakeJina
) -> None:
    await hold_papers_matching_the_query(database, jina)

    pages = [await client.post(SEARCH, json={"query": QUERY, "size": 2, "from": start, **mode}) for start in (0, 2)]

    # Each page counts every Paper that matches, not only those it holds, so that the pages can be counted.
    found = [page.json() for page in pages]
    assert [(page["search_mode"], page["total"], [hit["arxiv_id"] for hit in page["hits"]]) for page in found] == [
        (search_mode, 3, ["2411.00001", "2411.00002"]),
        (search_mode, 3, ["2411.00003"]),
    ]


@IN_BOTH_MODES
async def test_newest_first_orders_the_papers_by_their_publication_date(
    mode: dict[str, bool], search_mode: str, client: AsyncClient, database: BaseDatabase, jina: FakeJina
) -> None:
    await hold_papers_matching_the_query(database, jina)

    response = await client.post(SEARCH, json={"query": QUERY, "latest_papers": True, **mode})

    found = response.json()
    assert (found["search_mode"], [hit["arxiv_id"] for hit in found["hits"]]) == (
        search_mode,
        ["2411.00002", "2411.00003", "2411.00001"],
    )


@IN_BOTH_MODES
async def test_a_minimum_score_leaves_out_the_papers_whose_best_chunk_scores_below_it(
    mode: dict[str, bool], search_mode: str, client: AsyncClient, database: BaseDatabase, jina: FakeJina
) -> None:
    await hold_papers_matching_the_query(database, jina)
    without_a_minimum = await client.post(SEARCH, json={"query": QUERY, **mode})
    best, second_best = (hit["score"] for hit in without_a_minimum.json()["hits"][:2])

    response = await client.post(SEARCH, json={"query": QUERY, "min_score": (best + second_best) / 2, **mode})

    found = response.json()
    assert (found["search_mode"], found["total"], [hit["arxiv_id"] for hit in found["hits"]]) == (search_mode, 1, ["2411.00001"])


async def test_a_search_by_words_and_meaning_reaches_only_the_papers_of_the_chunks_each_half_keeps(
    each_half_keeping_100_chunks: None, client: AsyncClient, database: BaseDatabase, jina: FakeJina
) -> None:
    query = "turning one language into another"
    jina.embeds(query, as_vector=A_MEANING)
    jina.embeds("Neural machine translation", as_vector=A_MEANING)
    jina.embeds("Statistical machine translation", as_vector=NEAR_A_MEANING)
    ninety_nine_sections = [(f"Section {number}", words(100, about=f"part{number}word")) for number in range(99)]
    await hold_and_index(
        database,
        # 99 Chunks, one per section, which all hold its title, so are the 99 nearest to the query's meaning.
        paper("2411.00001", title="Neural machine translation", **content(*ninety_nine_sections)),
        # Its one Chunk is the 100th nearest, so the vector half keeps it.
        paper("2411.00002", title="Statistical machine translation"),
        # Its one Chunk is the 101st nearest, so the vector half does not, and no Chunk matches the query's words, so the
        # other half keeps none. With the course's multiplier, each half would keep 200 Chunks, and so this one too.
        paper("2411.00003"),
    )

    response = await client.post(SEARCH, json={"query": query})

    found = response.json()
    assert (found["search_mode"], found["total"], [hit["arxiv_id"] for hit in found["hits"]]) == (
        "hybrid",
        2,
        ["2411.00001", "2411.00002"],
    )


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


async def test_the_api_answers_another_request_while_a_search_waits_for_an_opensearch_that_does_not_answer(
    hanging_opensearch: HangingOpenSearch, client: AsyncClient
) -> None:
    async with hanging_opensearch.hanging() as asked:
        search = asyncio.create_task(client.post(SEARCH, json={"query": "attention"}))
        # Well before opensearch-py's timeout, 10 seconds, which a Search that stalls the API would wait out first.
        async with asyncio.timeout(5):
            await asked.wait()
            ping = await client.get(PING)

        assert (ping.status_code, search.done()) == (200, False)
    # OpenSearch then drops the Search's connection, so that the Search answers, and ends with the test.
    await search


async def test_the_week_3_search_endpoint_answers_404(client: AsyncClient) -> None:
    response = await client.post("/api/v1/search/", json={"query": "attention"})

    assert response.status_code == 404
