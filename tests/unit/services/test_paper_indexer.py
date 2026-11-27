"""
The indexing of Papers, through the indexing module, from a real PostgreSQL into a real OpenSearch, both disposable,
with Jina AI faked at its HTTP edge. Time is virtual, so waiting for Jina takes no real time.
"""

import asyncio
import re
from datetime import datetime
from itertools import pairwise
from typing import Any

import pytest
from opensearchpy import OpenSearch
from sqlalchemy import update
from src.config import Settings
from src.db.interfaces.base import BaseDatabase
from src.exceptions import OpenSearchException, PapersNotIndexed
from src.models.paper import Paper
from src.services.opensearch.index_config_hybrid import arxiv_papers_chunks_mapping
from src.services.paper_indexer import make_paper_indexer

from tests.fake_jina import Failure, FakeJina, own_vector
from tests.papers import chunk_index, content, hold, paper, words
from tests.unit.services.virtual_time import VirtualTimePolicy

# A Category OpenSearch rejects, as the mapping makes Categories keywords, and Lucene holds no term over 32766 bytes.
CATEGORY_TOO_LONG_FOR_A_KEYWORD = "cs." + "A" * 32766
# A section title OpenSearch rejects for the same reason, as each Chunk's section is a keyword too.
SECTION_TITLE_TOO_LONG_FOR_A_KEYWORD = "Results " + "A" * 32766
# A search pipeline's name OpenSearch refuses, as it refuses a request whose first line, which holds the name, is over
# 4096 bytes long.
PIPELINE_NAME_TOO_LONG_FOR_A_REQUEST_LINE = "hybrid-rrf-pipeline-" + "x" * 5000
# A meaning a test makes Jina embed a text as: a unit vector of jina-embeddings-v3's 1024 dimensions, as Jina's are.
A_MEANING = own_vector("a meaning", 1024)


def created_long_before(database: BaseDatabase, arxiv_id: str) -> None:
    """Date the Paper's row back to a day long before, as if that day's Ingestion had created it."""
    with database.get_session() as session:
        session.execute(update(Paper).where(Paper.arxiv_id == arxiv_id).values(created_at=datetime(2024, 11, 2, 6)))
        session.commit()


async def rebuild(database: BaseDatabase) -> dict[str, int]:
    """Rebuild the Index, with the settings the environment holds now."""
    return await make_paper_indexer(database, Settings()).rebuild_index()


async def index(database: BaseDatabase, arxiv_ids: list[str]) -> dict[str, int]:
    """Index the Papers with these arXiv IDs, with the settings the environment holds now."""
    return await make_paper_indexer(database, Settings()).index_papers(arxiv_ids)


def week3_index() -> str:
    """The Week 3 index, which held each Paper whole: the index name alone, arxiv-papers by default."""
    return Settings().opensearch.index_name


def chunk_hits(opensearch: OpenSearch, arxiv_id: str) -> list[dict[str, Any]]:
    """What the Index holds of each Chunk of the Paper with this arXiv ID, its ID included, in the order of their positions."""
    response = opensearch.search(
        index=chunk_index(), body={"query": {"term": {"arxiv_id": arxiv_id}}, "sort": [{"chunk_index": "asc"}], "size": 100}
    )
    hits: list[dict[str, Any]] = response["hits"]["hits"]
    return hits


def chunks(opensearch: OpenSearch, arxiv_id: str) -> list[dict[str, Any]]:
    """The Chunks the Index holds of the Paper with this arXiv ID, in the order of their positions."""
    return [hit["_source"] for hit in chunk_hits(opensearch, arxiv_id)]


def chunk_ids(opensearch: OpenSearch, arxiv_id: str) -> list[tuple[str, str | None]]:
    """
    The ID the Index holds each Chunk of the Paper with this arXiv ID under, and the ID the Chunk itself holds, in the
    order of their positions.
    """
    return [(hit["_id"], hit["_source"].get("chunk_id")) for hit in chunk_hits(opensearch, arxiv_id)]


def section_titles(opensearch: OpenSearch, *arxiv_ids: str) -> dict[str, list[str | None]]:
    """The section of each Chunk the Index holds of each Paper with these arXiv IDs, in the order of their positions."""
    return {arxiv_id: [chunk["section_title"] for chunk in chunks(opensearch, arxiv_id)] for arxiv_id in arxiv_ids}


def indexed_papers(opensearch: OpenSearch) -> list[str]:
    """The arXiv IDs of the Papers the Index holds Chunks of, in order."""
    response = opensearch.search(index=chunk_index(), body={"query": {"match_all": {}}, "size": 1000})
    return sorted({hit["_source"]["arxiv_id"] for hit in response["hits"]["hits"]})


def create_chunk_index(opensearch: OpenSearch) -> None:
    """Create the chunk index with the course's mapping, as the DAG's setup task does before each day's Ingestion."""
    mapping = arxiv_papers_chunks_mapping(vector_dimension=1024, vector_space_type="cosinesimil")
    opensearch.indices.create(index=chunk_index(), body=mapping)


@pytest.fixture(scope="module")
def event_loop_policy() -> asyncio.AbstractEventLoopPolicy:
    """pytest-asyncio runs each test of this module on a VirtualTimeLoop."""
    return VirtualTimePolicy()


async def test_each_chunk_made_from_a_papers_sections_begins_with_its_title_and_abstract(
    database: BaseDatabase, opensearch: OpenSearch
) -> None:
    sections = content(
        # Under 100 words each, so combined into one Chunk.
        ("Introduction", words(50, about="motivation")),
        ("Related Work", words(60, about="prior")),
        # From 100 to 800 words, so one Chunk.
        ("Method", words(300, about="method")),
        # Over 800 words, so split into windows of 600 words that overlap by 100: three Chunks.
        ("Experiments", words(1500, about="experiment")),
    )
    hold(database, paper("2411.00001", title="Attention over Retrieved Passages", abstract="We study attention.", **sections))

    await rebuild(database)

    header = "Attention over Retrieved Passages\n\nAbstract: We study attention.\n\n"
    assert [chunk["chunk_text"][: len(header)] for chunk in chunks(opensearch, "2411.00001")] == [header] * 5


async def test_each_chunk_holds_its_papers_pdf_link(database: BaseDatabase, opensearch: OpenSearch) -> None:
    two_sections = content(("Method", words(300, about="method")), ("Results", words(300, about="results")))
    hold(database, paper("2411.00001", version=2, **two_sections))

    await rebuild(database)

    assert [chunk.get("pdf_url") for chunk in chunks(opensearch, "2411.00001")] == ["https://arxiv.org/pdf/2411.00001v2"] * 2


async def test_each_chunk_is_identified_by_its_papers_arxiv_id_without_its_version_and_its_position_and_holds_that_id(
    database: BaseDatabase, opensearch: OpenSearch
) -> None:
    two_sections = content(("Method", words(300, about="method")), ("Results", words(300, about="results")))
    hold(database, paper("2411.00001", version=2, **two_sections))

    await rebuild(database)

    assert chunk_ids(opensearch, "2411.00001") == [("2411.00001_0", "2411.00001_0"), ("2411.00001_1", "2411.00001_1")]


async def test_with_section_based_chunking_off_a_papers_content_is_chunked_into_plain_word_windows(
    database: BaseDatabase, opensearch: OpenSearch, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHUNKING__SECTION_BASED", "false")
    sections = [{"title": "Method", "content": words(300, about="method")}]
    hold(database, paper("2411.00001", raw_text=words(700, about="content"), sections=sections, pdf_processed=True))

    await rebuild(database)

    # Windows of 600 words that overlap by 100: the Content's first 600 words, then its last 200, with no section.
    windows = [(chunk["section_title"], chunk["chunk_word_count"]) for chunk in chunks(opensearch, "2411.00001")]
    assert windows == [(None, 600), (None, 200)]


async def test_no_chunk_holds_a_section_titled_references_or_bibliography(database: BaseDatabase, opensearch: OpenSearch) -> None:
    with_references = content(
        ("Method", words(300, about="method")),
        ("References", words(300, about="cited")),
        # The Paper's own text, though it comes after the works it cites.
        ("Appendix", words(300, about="appendix")),
    )
    with_bibliography = content(("Method", words(300, about="method")), ("Bibliography", words(300, about="cited")))
    hold(database, paper("2411.00001", **with_references), paper("2411.00002", **with_bibliography))

    await rebuild(database)

    sections = section_titles(opensearch, "2411.00001", "2411.00002")
    assert sections == {"2411.00001": ["Method", "Appendix"], "2411.00002": ["Method"]}


async def test_no_chunk_holds_a_bibliography_whose_title_is_in_capitals_or_numbered_but_a_title_mentioning_references_stays(
    database: BaseDatabase, opensearch: OpenSearch
) -> None:
    # As Docling reads the titles of some Papers' bibliographies.
    in_capitals = content(("Method", words(300, about="method")), ("REFERENCES", words(300, about="cited")))
    numbered = content(("Method", words(300, about="method")), ("6. References", words(300, about="cited")))
    # The Paper's own text, about the reference answers it compares its outputs with.
    mentioning_references = content(("Method", words(300, about="method")), ("4.3 Multiple References", words(300, about="gold")))
    hold(
        database,
        paper("2411.00001", **in_capitals),
        paper("2411.00002", **numbered),
        paper("2411.00003", **mentioning_references),
    )

    await rebuild(database)

    sections = section_titles(opensearch, "2411.00001", "2411.00002", "2411.00003")
    assert sections == {"2411.00001": ["Method"], "2411.00002": ["Method"], "2411.00003": ["Method", "4.3 Multiple References"]}


async def test_sections_that_share_a_title_are_all_kept_each_in_its_place(database: BaseDatabase, opensearch: OpenSearch) -> None:
    sections = content(
        # Each case study ends with its summary, under the same title, as some Papers' subsections share theirs.
        ("Case Study: Code Review", words(300, about="review")),
        ("Summary", words(300, about="lesson")),
        ("Case Study: Pair Programming", words(300, about="pairing")),
        ("Summary", words(300, about="takeaway")),
    )
    hold(database, paper("2411.00001", **sections))

    await rebuild(database)

    # The last word a Chunk holds tells whose text it is.
    sections_and_last_words = [
        (chunk["section_title"], chunk["chunk_text"].split()[-1]) for chunk in chunks(opensearch, "2411.00001")
    ]
    assert sections_and_last_words == [
        ("Case Study: Code Review", "review299"),
        ("Summary", "lesson299"),
        ("Case Study: Pair Programming", "pairing299"),
        ("Summary", "takeaway299"),
    ]


async def test_the_sections_a_chunk_combines_are_separated_by_blank_lines_not_by_a_literal_backslash_n(
    database: BaseDatabase, opensearch: OpenSearch
) -> None:
    introduction, related_work = words(50, about="motivation"), words(60, about="prior")
    method, conclusion = words(300, about="method"), words(30, about="conclusion")
    sections = content(
        # Under 100 words each, so combined into one Chunk.
        ("Introduction", introduction),
        ("Related Work", related_work),
        ("Method", method),
        # Under 100 words, and under 200 with the title and abstract, so merged into the Chunk before it.
        ("Conclusion", conclusion),
    )
    hold(database, paper("2411.00001", **sections))

    await rebuild(database)

    header = "The title of 2411.00001\n\nAbstract: The abstract of 2411.00001.\n\n"
    assert [chunk["chunk_text"] for chunk in chunks(opensearch, "2411.00001")] == [
        f"{header}Section: Introduction\n\n{introduction}\n\nSection: Related Work\n\n{related_work}",
        f"{header}Section: Method\n\n{method}\n\nSection: Conclusion\n\n{conclusion}",
    ]


async def test_a_short_content_without_sections_is_indexed_as_one_chunk_of_its_words(
    database: BaseDatabase, opensearch: OpenSearch
) -> None:
    # Fewer words than the 100 a Chunk should hold at least, and no section to chunk them by.
    hold(database, paper("2411.00001", raw_text=words(40, about="content"), sections=[], pdf_processed=True))

    await rebuild(database)

    assert [chunk["chunk_text"] for chunk in chunks(opensearch, "2411.00001")] == [words(40, about="content")]


async def test_after_a_rebuild_the_index_holds_every_paper_postgresql_holds_and_the_rebuild_counts_their_papers_and_chunks(
    database: BaseDatabase, opensearch: OpenSearch
) -> None:
    two_sections = content(("Method", words(300, about="method")), ("Results", words(300, about="results")))
    hold(database, paper("2411.00001", version=2, **two_sections), paper("2411.00002"), paper("2411.00003"))

    counts = await rebuild(database)

    # A Chunk for each of the first Paper's two sections, and one for each Paper without Content, its title and abstract.
    assert counts == {"papers_held": 3, "papers_indexed": 3, "chunks_indexed": 4}
    assert indexed_papers(opensearch) == ["2411.00001", "2411.00002", "2411.00003"]


async def test_a_rebuild_replaces_an_index_created_with_a_different_mapping_by_one_with_the_courses_mapping_and_faiss(
    database: BaseDatabase, opensearch: OpenSearch
) -> None:
    # The chunk index as a different mapping created it, where the title was a keyword rather than text.
    opensearch.indices.create(index=chunk_index(), body={"mappings": {"properties": {"title": {"type": "keyword"}}}})
    hold(database, paper("2411.00001"))

    await rebuild(database)

    mapping = opensearch.indices.get_mapping(index=chunk_index())[chunk_index()]["mappings"]
    assert mapping == arxiv_papers_chunks_mapping(vector_dimension=1024, vector_space_type="cosinesimil")["mappings"]
    # The course's HNSW parameters and cosine similarity, with faiss instead of nmslib.
    assert mapping["properties"]["embedding"]["method"] == {
        "name": "hnsw",
        "engine": "faiss",
        "space_type": "cosinesimil",
        "parameters": {"m": 16, "ef_construction": 512},
    }


async def test_a_rebuild_deletes_the_week_3_index_and_indexes_every_paper_into_the_chunk_index(
    database: BaseDatabase, opensearch: OpenSearch
) -> None:
    # As Week 3's indexing left it, holding a Paper whole.
    opensearch.index(index=week3_index(), body={"arxiv_id": "2411.00001", "title": "The title of 2411.00001"}, refresh=True)
    hold(database, paper("2411.00001"))

    await rebuild(database)

    assert not opensearch.indices.exists(index=week3_index())
    assert indexed_papers(opensearch) == ["2411.00001"]


async def test_a_rebuild_creates_the_chunk_index_with_vectors_of_the_dimension_and_space_type_the_settings_give(
    database: BaseDatabase, opensearch: OpenSearch, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENSEARCH__VECTOR_DIMENSION", "8")
    monkeypatch.setenv("OPENSEARCH__VECTOR_SPACE_TYPE", "l2")

    await rebuild(database)

    embedding = opensearch.indices.get_mapping(index=chunk_index())[chunk_index()]["mappings"]["properties"]["embedding"]
    assert (embedding["dimension"], embedding["method"]["space_type"]) == (8, "l2")


async def test_a_rebuild_embeds_each_chunk_as_a_vector_of_the_dimension_the_settings_give(
    database: BaseDatabase, opensearch: OpenSearch, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENSEARCH__VECTOR_DIMENSION", "8")
    hold(database, paper("2411.00001"))

    await rebuild(database)

    assert [len(chunk["embedding"]) for chunk in chunks(opensearch, "2411.00001")] == [8]


async def test_a_rebuild_that_cannot_recreate_the_chunk_index_fails_without_indexing_a_paper(
    database: BaseDatabase, opensearch: OpenSearch
) -> None:
    # The chunk index as an alias of another index, which OpenSearch refuses to delete through the alias.
    opensearch.indices.create(index="arxiv-papers-chunks-v1", body={"aliases": {chunk_index(): {}}})
    hold(database, paper("2411.00001"))

    with pytest.raises(OpenSearchException, match=chunk_index()):
        await rebuild(database)

    assert opensearch.count(index="arxiv-papers-chunks-v1")["count"] == 0


async def test_a_rebuild_that_cannot_recreate_the_rrf_pipeline_fails_without_indexing_a_paper(
    database: BaseDatabase, opensearch: OpenSearch, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Every Search by meaning goes through the pipeline, so without it each answers 503.
    monkeypatch.setenv("OPENSEARCH__RRF_PIPELINE_NAME", PIPELINE_NAME_TOO_LONG_FOR_A_REQUEST_LINE)
    hold(database, paper("2411.00001"))

    with pytest.raises(PapersNotIndexed, match="RRF pipeline"):
        await rebuild(database)

    assert opensearch.count(index=chunk_index())["count"] == 0


async def test_a_paper_held_without_content_and_one_with_empty_content_are_each_indexed_as_one_chunk_their_title_and_abstract(
    database: BaseDatabase, opensearch: OpenSearch
) -> None:
    hold(database, paper("2411.00001", raw_text=None), paper("2411.00002", raw_text="", sections=[], pdf_processed=True))

    await rebuild(database)

    arxiv_ids = ["2411.00001", "2411.00002"]
    texts = {arxiv_id: [chunk["chunk_text"] for chunk in chunks(opensearch, arxiv_id)] for arxiv_id in arxiv_ids}
    assert texts == {
        "2411.00001": ["The title of 2411.00001\n\nAbstract: The abstract of 2411.00001."],
        "2411.00002": ["The title of 2411.00002\n\nAbstract: The abstract of 2411.00002."],
    }


async def test_indexing_given_arxiv_ids_indexes_exactly_those_papers_and_no_other_paper_the_curator_holds(
    database: BaseDatabase, opensearch: OpenSearch
) -> None:
    create_chunk_index(opensearch)
    hold(database, paper("2411.00001"), paper("2411.00002"), paper("2411.00003"))

    counts = await index(database, ["2411.00001", "2411.00003"])

    assert counts == {"papers_to_index": 2, "papers_indexed": 2, "chunks_indexed": 2}
    assert indexed_papers(opensearch) == ["2411.00001", "2411.00003"]


async def test_a_paper_updated_to_a_new_version_that_gains_chunks_has_all_its_new_ones_none_duplicated(
    database: BaseDatabase, opensearch: OpenSearch
) -> None:
    create_chunk_index(opensearch)
    hold(database, paper("2411.00001", version=1, **content(("Earlier Method", words(300, about="method")))))
    await index(database, ["2411.00001"])
    # Today's Ingestion stores its new Version, with three sections, which updates the Paper's row.
    new_version = content(
        ("Revised Method", words(300, about="revision")),
        ("Revised Results", words(300, about="results")),
        ("Revised Discussion", words(300, about="discussion")),
    )
    hold(database, paper("2411.00001", version=2, **new_version))

    await index(database, ["2411.00001"])

    sections = [chunk["section_title"] for chunk in chunks(opensearch, "2411.00001")]
    assert sections == ["Revised Method", "Revised Results", "Revised Discussion"]


async def test_a_paper_updated_to_a_new_version_that_loses_chunks_keeps_only_its_new_ones_none_left_over(
    database: BaseDatabase, opensearch: OpenSearch
) -> None:
    create_chunk_index(opensearch)
    # Ingested and indexed long before, so its row was created then, with a Chunk for each of its three sections.
    first_version = content(
        ("Earlier Method", words(300, about="method")),
        ("Earlier Results", words(300, about="results")),
        ("Earlier Discussion", words(300, about="discussion")),
    )
    hold(database, paper("2411.00001", version=1, **first_version))
    created_long_before(database, "2411.00001")
    await index(database, ["2411.00001"])
    # Today's Ingestion stores its new Version, with one section, which updates the Paper's row.
    hold(database, paper("2411.00001", version=2, **content(("Revised Method", words(300, about="revision")))))

    await index(database, ["2411.00001"])

    assert [chunk["section_title"] for chunk in chunks(opensearch, "2411.00001")] == ["Revised Method"]


async def test_indexing_a_paper_again_deletes_any_of_its_chunks_an_index_built_before_chunk_ids_holds(
    database: BaseDatabase, opensearch: OpenSearch
) -> None:
    create_chunk_index(opensearch)
    hold(database, paper("2411.00001"))
    # As an Index built before each Chunk had an ID holds it: under a random ID OpenSearch picked, without chunk_id.
    opensearch.index(
        index=chunk_index(),
        body={"arxiv_id": "2411.00001", "chunk_index": 0, "chunk_text": "The title of 2411.00001"},
        refresh=True,
    )

    await index(database, ["2411.00001"])

    assert chunk_ids(opensearch, "2411.00001") == [("2411.00001_0", "2411.00001_0")]


async def test_a_paper_whose_new_version_opensearch_rejects_keeps_its_previous_chunks(
    database: BaseDatabase, opensearch: OpenSearch
) -> None:
    create_chunk_index(opensearch)
    first_version = content(
        ("Earlier Method", words(300, about="method")),
        ("Earlier Results", words(300, about="results")),
        ("Earlier Discussion", words(300, about="discussion")),
    )
    hold(database, paper("2411.00001", version=1, **first_version))
    await index(database, ["2411.00001"])
    # Its new Version, with one section, is listed in a Category OpenSearch rejects, which each of its Chunks holds.
    new_version = content(("Revised Method", words(300, about="revision")))
    hold(database, paper("2411.00001", version=2, categories=[CATEGORY_TOO_LONG_FOR_A_KEYWORD], **new_version))

    with pytest.raises(PapersNotIndexed):
        await index(database, ["2411.00001"])

    sections = [chunk["section_title"] for chunk in chunks(opensearch, "2411.00001")]
    assert sections == ["Earlier Method", "Earlier Results", "Earlier Discussion"]


async def test_when_opensearch_fails_to_delete_a_papers_chunks_left_over_indexing_fails_naming_it_and_counts_its_new_chunks(
    database: BaseDatabase, opensearch: OpenSearch
) -> None:
    create_chunk_index(opensearch)
    first_version = content(
        ("Earlier Method", words(300, about="method")),
        ("Earlier Results", words(300, about="results")),
        ("Earlier Discussion", words(300, about="discussion")),
    )
    hold(database, paper("2411.00001", version=1, **first_version))
    await index(database, ["2411.00001"])
    new_version = content(("Revised Method", words(300, about="revision")), ("Revised Results", words(300, about="outcome")))
    hold(database, paper("2411.00001", version=2, **new_version))
    # OpenSearch now refuses to search the Index, which deleting by a query needs, though it still writes into it.
    opensearch.indices.put_settings(index=chunk_index(), body={"index.blocks.read": True})

    # Its two new Chunks, which the Index holds, with its previous third Chunk left over.
    with pytest.raises(PapersNotIndexed, match=re.escape("as 2 Chunks, but could not index 2411.00001")):
        await index(database, ["2411.00001"])


async def test_when_opensearch_rejects_a_chunk_indexing_given_arxiv_ids_indexes_the_other_papers_then_fails_naming_its_paper(
    database: BaseDatabase, opensearch: OpenSearch
) -> None:
    create_chunk_index(opensearch)
    hold(database, paper("2411.00001"), paper("2411.00002", categories=[CATEGORY_TOO_LONG_FOR_A_KEYWORD]), paper("2411.00003"))

    with pytest.raises(PapersNotIndexed, match=re.escape("2411.00002")):
        await index(database, ["2411.00001", "2411.00002", "2411.00003"])

    assert indexed_papers(opensearch) == ["2411.00001", "2411.00003"]


async def test_when_opensearch_rejects_a_chunk_the_failure_counts_the_papers_indexed(
    database: BaseDatabase, opensearch: OpenSearch
) -> None:
    create_chunk_index(opensearch)
    hold(database, paper("2411.00001"), paper("2411.00002", categories=[CATEGORY_TOO_LONG_FOR_A_KEYWORD]), paper("2411.00003"))

    with pytest.raises(PapersNotIndexed) as failure:
        await index(database, ["2411.00001", "2411.00002", "2411.00003"])

    # The daily report shows it, as a failed task returns no counts.
    assert failure.value.papers_indexed == 2


async def test_when_opensearch_rejects_one_of_a_papers_chunks_the_failure_counts_those_it_indexed(
    database: BaseDatabase, opensearch: OpenSearch
) -> None:
    create_chunk_index(opensearch)
    two_sections = content(
        ("Method", words(300, about="method")), (SECTION_TITLE_TOO_LONG_FOR_A_KEYWORD, words(300, about="results"))
    )
    hold(database, paper("2411.00001"), paper("2411.00002", **two_sections))

    # The first Paper's one Chunk, and the second Paper's Chunk OpenSearch accepted, which the Index now holds.
    with pytest.raises(PapersNotIndexed, match=re.escape("as 2 Chunks, but could not index 2411.00002")):
        await index(database, ["2411.00001", "2411.00002"])


async def test_when_opensearch_rejects_a_chunk_a_rebuild_indexes_the_other_papers_then_fails_naming_its_paper(
    database: BaseDatabase, opensearch: OpenSearch
) -> None:
    hold(database, paper("2411.00001"), paper("2411.00002", categories=[CATEGORY_TOO_LONG_FOR_A_KEYWORD]), paper("2411.00003"))

    with pytest.raises(PapersNotIndexed, match=re.escape("2411.00002")):
        await rebuild(database)

    assert indexed_papers(opensearch) == ["2411.00001", "2411.00003"]


async def test_with_the_chunk_index_missing_indexing_given_arxiv_ids_fails_without_indexing_a_paper_or_creating_the_index(
    database: BaseDatabase, opensearch: OpenSearch
) -> None:
    hold(database, paper("2411.00001"))

    with pytest.raises(PapersNotIndexed, match=chunk_index()):
        await index(database, ["2411.00001"])

    assert not opensearch.indices.exists(index=chunk_index())


async def test_with_opensearch_not_answering_indexing_given_arxiv_ids_fails(
    database: BaseDatabase, opensearch_down: None
) -> None:
    hold(database, paper("2411.00001"))

    with pytest.raises(PapersNotIndexed):
        await index(database, ["2411.00001"])


async def test_with_opensearch_not_answering_a_rebuild_fails(database: BaseDatabase, opensearch_down: None) -> None:
    hold(database, paper("2411.00001"))

    with pytest.raises(PapersNotIndexed):
        await rebuild(database)


async def test_a_paper_whose_embedding_keeps_failing_keeps_its_previous_chunks_while_indexing_goes_on_then_fails_naming_it(
    database: BaseDatabase, opensearch: OpenSearch, jina: FakeJina
) -> None:
    create_chunk_index(opensearch)
    first_version = content(
        ("Earlier Method", words(300, about="method")),
        ("Earlier Results", words(300, about="results")),
        ("Earlier Discussion", words(300, about="discussion")),
    )
    hold(database, paper("2411.00002", version=1, **first_version))
    await index(database, ["2411.00002"])
    # Today's Ingestion stores two new Papers, and its new Version, with one section.
    new_version = content(("Revised Method", words(300, about="revision")))
    hold(database, paper("2411.00001"), paper("2411.00002", version=2, **new_version), paper("2411.00003"))
    # Each of a Paper's Chunks begins with its title, which holds its arXiv ID.
    jina.keeps_failing(Failure.SERVER_ERROR, on="2411.00002")

    with pytest.raises(PapersNotIndexed, match=re.escape("could not index 2411.00002")):
        await index(database, ["2411.00001", "2411.00002", "2411.00003"])

    sections = section_titles(opensearch, "2411.00001", "2411.00002", "2411.00003")
    # Each new Paper's one Chunk, its title and abstract, has no section.
    assert sections == {
        "2411.00001": [None],
        "2411.00002": ["Earlier Method", "Earlier Results", "Earlier Discussion"],
        "2411.00003": [None],
    }


async def test_embedding_is_retried_through_rate_limits_server_errors_and_timeouts_and_the_paper_is_then_indexed(
    database: BaseDatabase, opensearch: OpenSearch, jina: FakeJina
) -> None:
    create_chunk_index(opensearch)
    hold(database, paper("2411.00001"))
    jina.embeds("2411.00001", as_vector=A_MEANING)
    jina.fails_next(Failure.RATE_LIMIT, Failure.SERVER_ERROR, Failure.TIMEOUT)

    await index(database, ["2411.00001"])

    # Its one Chunk, its title and abstract, with the embedding Jina made of it and the model that made it.
    embeddings = [(chunk["embedding"], chunk["embedding_model"]) for chunk in chunks(opensearch, "2411.00001")]
    assert embeddings == [(A_MEANING, "jina-embeddings-v3")]


async def test_embedding_waits_out_a_rate_limit_that_lasts_a_minute_and_the_paper_is_then_indexed(
    database: BaseDatabase, opensearch: OpenSearch, jina: FakeJina
) -> None:
    create_chunk_index(opensearch)
    hold(database, paper("2411.00001"))
    # As when a free key has spent its 100,000 tokens for the minute.
    jina.rate_limits_for(60)

    await index(database, ["2411.00001"])

    assert indexed_papers(opensearch) == ["2411.00001"]


async def test_after_a_rate_limit_embedding_asks_jina_again_only_once_the_wait_jina_asked_for_is_over(
    database: BaseDatabase, opensearch: OpenSearch, jina: FakeJina
) -> None:
    create_chunk_index(opensearch)
    hold(database, paper("2411.00001"))
    jina.rate_limits_for(45, saying_how_long=True)

    await index(database, ["2411.00001"])

    # Jina rate-limited the first request, saying to wait 45 seconds, and answered the next, which came then.
    first, *later = jina.request_times
    assert [time - first for time in later] == [45]


async def test_with_an_invalid_jina_key_indexing_fails_each_paper_at_its_first_call_to_jina_without_retrying(
    database: BaseDatabase, opensearch: OpenSearch, jina: FakeJina
) -> None:
    create_chunk_index(opensearch)
    hold(database, paper("2411.00001"), paper("2411.00002"))
    jina.keeps_failing(Failure.INVALID_KEY)

    with pytest.raises(PapersNotIndexed):
        await index(database, ["2411.00001", "2411.00002"])

    # One call for each Paper, as no retry would make the key valid.
    assert len(jina.requests) == 2


async def test_a_rebuild_closes_its_jina_client_when_it_ends(
    database: BaseDatabase, opensearch: OpenSearch, jina: FakeJina
) -> None:
    hold(database, paper("2411.00001"))

    await rebuild(database)

    assert jina.closed


async def test_indexing_given_arxiv_ids_closes_its_jina_client_when_it_ends_even_in_failure(
    database: BaseDatabase, opensearch: OpenSearch, jina: FakeJina
) -> None:
    create_chunk_index(opensearch)
    hold(database, paper("2411.00001"))
    jina.keeps_failing(Failure.INVALID_KEY)

    with pytest.raises(PapersNotIndexed):
        await index(database, ["2411.00001"])

    assert jina.closed


async def test_embedding_waits_no_more_than_a_minute_at_a_time_whatever_jina_asks(
    database: BaseDatabase, opensearch: OpenSearch, jina: FakeJina
) -> None:
    create_chunk_index(opensearch)
    hold(database, paper("2411.00001"))
    # Longer than the minute over which Jina counts a free key's tokens, as a Retry-After header gone wrong might ask.
    jina.rate_limits_for(90, saying_how_long=True)

    await index(database, ["2411.00001"])

    assert indexed_papers(opensearch) == ["2411.00001"]
    assert max(later - earlier for earlier, later in pairwise(jina.request_times)) <= 60
