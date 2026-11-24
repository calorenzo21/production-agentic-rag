"""The indexing of Papers, through the indexing module, from a real PostgreSQL into a real OpenSearch, both disposable."""

from datetime import datetime
from typing import Any

import pytest
from opensearchpy import OpenSearch
from sqlalchemy import update
from src.config import Settings
from src.db.interfaces.base import BaseDatabase
from src.exceptions import OpenSearchException
from src.models.paper import Paper
from src.repositories.paper import PaperRepository
from src.schemas.arxiv.paper import PaperCreate
from src.services.opensearch.index_config import ARXIV_PAPERS_MAPPING
from src.services.paper_indexer import make_paper_indexer


def paper(arxiv_id: str, *, version: int = 1, **fields: Any) -> PaperCreate:
    """A Paper as the Ingestion stores it, held at its Version, without Content unless the test gives it."""
    metadata: dict[str, Any] = {
        "arxiv_id": arxiv_id,
        "version": version,
        "title": f"The title of {arxiv_id}",
        "authors": ["Ada Lovelace", "Charles Babbage"],
        "abstract": f"The abstract of {arxiv_id}.",
        "categories": ["cs.AI"],
        "published_date": datetime(2024, 11, 1, 9, 30),
        "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}v{version}",
    }
    return PaperCreate(**{**metadata, **fields})


def hold(database: BaseDatabase, *papers: PaperCreate) -> None:
    """Store the Papers as the Ingestion does."""
    with database.get_session() as session:
        repository = PaperRepository(session)
        for held in papers:
            repository.upsert(held)


def created_long_before(database: BaseDatabase, arxiv_id: str) -> None:
    """Date the Paper's row back to a day long before, as if that day's Ingestion had created it."""
    with database.get_session() as session:
        session.execute(update(Paper).where(Paper.arxiv_id == arxiv_id).values(created_at=datetime(2024, 11, 2, 6)))
        session.commit()


def rebuild(database: BaseDatabase) -> dict[str, int]:
    """Rebuild the Index, with the settings the environment holds now."""
    return make_paper_indexer(database, Settings()).rebuild_index()


def index(database: BaseDatabase, arxiv_ids: list[str]) -> dict[str, int]:
    """Index the Papers with these arXiv IDs, with the settings the environment holds now."""
    return make_paper_indexer(database, Settings()).index_papers(arxiv_ids)


def index_name() -> str:
    return Settings().opensearch.index_name


def create_index(opensearch: OpenSearch) -> None:
    """Create the Index with the course's mapping, as the DAG's setup task does before each day's Ingestion."""
    opensearch.indices.create(index=index_name(), body=ARXIV_PAPERS_MAPPING)


def entry_ids(opensearch: OpenSearch) -> list[str]:
    """The arXiv IDs the Index's entries are keyed on, in order."""
    response = opensearch.search(index=index_name(), body={"query": {"match_all": {}}, "size": 100})
    return sorted(hit["_id"] for hit in response["hits"]["hits"])


def found_by_title(opensearch: OpenSearch, words: str) -> list[str]:
    """The arXiv IDs of the entries whose title matches the words."""
    response = opensearch.search(index=index_name(), body={"query": {"match": {"title": words}}})
    return [hit["_id"] for hit in response["hits"]["hits"]]


def test_after_a_rebuild_the_index_holds_as_many_papers_as_postgresql_one_entry_per_paper(
    database: BaseDatabase, opensearch: OpenSearch
) -> None:
    hold(database, paper("2411.00001", version=2), paper("2411.00002"), paper("2411.00003"))

    counts = rebuild(database)

    assert counts == {"papers_held": 3, "papers_indexed": 3}
    assert entry_ids(opensearch) == ["2411.00001", "2411.00002", "2411.00003"]


def test_a_rebuild_replaces_an_index_created_with_a_different_mapping_by_one_with_the_courses_mapping(
    database: BaseDatabase, opensearch: OpenSearch
) -> None:
    # The Index as a different mapping created it, where the title was a keyword rather than text.
    opensearch.indices.create(index=index_name(), body={"mappings": {"properties": {"title": {"type": "keyword"}}}})
    hold(database, paper("2411.00001"))

    rebuild(database)

    mapping = opensearch.indices.get_mapping(index=index_name())[index_name()]["mappings"]
    assert mapping == ARXIV_PAPERS_MAPPING["mappings"]


def test_a_rebuild_that_cannot_recreate_the_index_fails_without_indexing_a_paper(
    database: BaseDatabase, opensearch: OpenSearch
) -> None:
    # The Index as an alias of another index, which OpenSearch refuses to delete through the alias.
    opensearch.indices.create(index="arxiv-papers-v1", body={"aliases": {index_name(): {}}})
    hold(database, paper("2411.00001"))

    with pytest.raises(OpenSearchException, match=index_name()):
        rebuild(database)

    assert opensearch.count(index="arxiv-papers-v1")["count"] == 0


def test_a_paper_without_content_is_indexed_and_found_by_its_title(database: BaseDatabase, opensearch: OpenSearch) -> None:
    hold(database, paper("2411.00001", title="Lexical Search over the Papers whose PDF failed to parse", raw_text=None))

    rebuild(database)

    assert found_by_title(opensearch, "lexical") == ["2411.00001"]


def test_content_longer_than_the_maximum_content_size_is_capped(
    database: BaseDatabase, opensearch: OpenSearch, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENSEARCH__MAX_TEXT_SIZE", "14")
    hold(database, paper("2411.00001", raw_text="The Content of a long Paper.", pdf_processed=True))

    rebuild(database)

    assert opensearch.get(index=index_name(), id="2411.00001")["_source"]["raw_text"] == "The Content of"


def test_indexing_given_arxiv_ids_indexes_exactly_those_papers_and_no_other_paper_the_curator_holds(
    database: BaseDatabase, opensearch: OpenSearch
) -> None:
    create_index(opensearch)
    hold(database, paper("2411.00001"), paper("2411.00002"), paper("2411.00003"))

    counts = index(database, ["2411.00001", "2411.00003"])

    assert counts == {"papers_to_index": 2, "papers_indexed": 2}
    assert entry_ids(opensearch) == ["2411.00001", "2411.00003"]


def test_a_paper_updated_to_a_new_version_is_indexed_with_its_new_title_and_abstract_still_as_one_entry(
    database: BaseDatabase, opensearch: OpenSearch
) -> None:
    create_index(opensearch)
    # Ingested and indexed long before, so its row was created then.
    hold(database, paper("2411.00001", version=1, title="A first title", abstract="A first abstract."))
    created_long_before(database, "2411.00001")
    index(database, ["2411.00001"])
    # Today's Ingestion stores its new Version, which updates the Paper's row.
    hold(database, paper("2411.00001", version=2, title="A revised title", abstract="A revised abstract."))

    index(database, ["2411.00001"])

    assert entry_ids(opensearch) == ["2411.00001"]
    entry = opensearch.get(index=index_name(), id="2411.00001")["_source"]
    assert (entry["title"], entry["abstract"]) == ("A revised title", "A revised abstract.")
