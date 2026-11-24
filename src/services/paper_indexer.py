"""
The indexing of Papers into the Index. The course indexes them in the DAG's task, and again through an unused path in
the metadata fetcher. Here this module alone indexes them, so that a Paper is indexed the same way whoever asks.
"""

import logging
from typing import Any

from sqlalchemy import Row, Select, select
from src.config import Settings, get_settings
from src.db.interfaces.base import BaseDatabase
from src.exceptions import OpenSearchException, PapersNotIndexed
from src.models.paper import Paper
from src.services.opensearch.client import OpenSearchClient

logger = logging.getLogger(__name__)

# What a Paper's entry holds, read without its sections and references, which can be as large as its text.
ENTRY_COLUMNS = (
    Paper.arxiv_id,
    Paper.title,
    Paper.authors,
    Paper.abstract,
    Paper.categories,
    Paper.raw_text,
    Paper.pdf_url,
    Paper.published_date,
    Paper.created_at,
    Paper.updated_at,
)

# How many Papers to read from PostgreSQL at a time, so that indexing never holds every Paper's Content at once.
BATCH_SIZE = 100


class PaperIndexer:
    """Indexes the Papers the curator holds into the Index, one entry per Paper, keyed on its arXiv ID without its Version."""

    def __init__(self, database: BaseDatabase, opensearch_client: OpenSearchClient, max_content_size: int) -> None:
        self.database = database
        self.opensearch_client = opensearch_client
        self.max_content_size = max_content_size

    def rebuild_index(self) -> dict[str, int]:
        """
        Recreate the Index with the course's mapping, then index every Paper the curator holds, and count the Papers
        held and those indexed. Until it ends, Searches find fewer Papers, as the Index starts empty.

        Raises PapersNotIndexed before indexing any Paper when OpenSearch does not recreate the Index, as when it does not
        answer. Otherwise it indexes every Paper it can, then raises it naming the others.
        """
        if not self.opensearch_client.create_index(force=True):
            # The client logs why. Whatever Index is left may have another mapping, or none, which indexing would create.
            raise PapersNotIndexed(
                f"Could not recreate the Index {self.opensearch_client.index_name}, so indexed no Paper", papers_indexed=0
            )
        held, not_indexed = self._index(select(*ENTRY_COLUMNS))
        indexed = held - len(not_indexed)
        self._log_or_fail(
            f"Indexed {indexed} of the {held} Papers the curator holds into {self.opensearch_client.index_name}",
            indexed,
            not_indexed,
        )
        return {"papers_held": held, "papers_indexed": indexed}

    def index_papers(self, arxiv_ids: list[str]) -> dict[str, int]:
        """
        Index the Papers with these arXiv IDs, without their Versions, into the Index, and count the Papers to index and
        those indexed. The Index must exist, as the DAG's setup task and the API's startup create it.

        Raises PapersNotIndexed before indexing any Paper when OpenSearch does not answer or the Index does not exist.
        Otherwise it indexes every Paper it can, then raises it naming the others.
        """
        try:
            index_exists = self.opensearch_client.index_exists()
        except OpenSearchException as e:
            raise PapersNotIndexed(
                f"Could not check that the Index {self.opensearch_client.index_name} exists, so indexed none of the "
                f"{len(arxiv_ids)} Papers to index: {e}",
                papers_indexed=0,
            ) from e
        if not index_exists:
            # OpenSearch would create it for the first Paper, with a mapping it guesses, where the Category filter matches
            # nothing. Rebuilding the Index creates it with the course's mapping.
            raise PapersNotIndexed(
                f"The Index {self.opensearch_client.index_name} does not exist, so indexed none of the {len(arxiv_ids)} "
                "Papers to index",
                papers_indexed=0,
            )
        found, not_indexed = self._index(select(*ENTRY_COLUMNS).where(Paper.arxiv_id.in_(arxiv_ids)))
        indexed = found - len(not_indexed)
        self._log_or_fail(
            f"Indexed {indexed} of the {len(arxiv_ids)} Papers to index into {self.opensearch_client.index_name}",
            indexed,
            not_indexed,
        )
        return {"papers_to_index": len(arxiv_ids), "papers_indexed": indexed}

    @staticmethod
    def _log_or_fail(summary: str, indexed: int, not_indexed: list[str]) -> None:
        """Log the summary of an indexing, or raise it as PapersNotIndexed when the indexing left Papers out, naming them."""
        if not_indexed:
            raise PapersNotIndexed(f"{summary}, but could not index {', '.join(not_indexed)}", papers_indexed=indexed)
        logger.info(summary)

    def _index(self, statement: Select[Any]) -> tuple[int, list[str]]:
        """
        Index the Papers the statement selects, reading BATCH_SIZE at a time, and count those selected. One Paper that
        cannot be indexed does not stop the others, so it also returns the arXiv IDs of those it could not index. Neither
        does an OpenSearch that stops answering midway: each Paper left then fails on its own, after the client's timeout.
        """
        selected = 0
        not_indexed: list[str] = []
        with self.database.get_session() as session:
            for paper in session.execute(statement.execution_options(yield_per=BATCH_SIZE)):
                selected += 1
                # The client logs why, whether OpenSearch rejected the Paper or did not answer.
                if not self.opensearch_client.index_paper(self._entry(paper)):
                    not_indexed.append(paper.arxiv_id)
        return selected, not_indexed

    def _entry(self, paper: Row[Any]) -> dict[str, Any]:
        """A Paper's entry in the Index: the course's, with its Content capped as the course's metadata fetcher caps it."""
        return {
            "arxiv_id": paper.arxiv_id,
            "title": paper.title,
            "authors": ", ".join(paper.authors),
            "abstract": paper.abstract,
            "categories": paper.categories,
            # A Paper without Content is indexed with the rest, as the course does.
            "raw_text": (paper.raw_text or "")[: self.max_content_size],
            "pdf_url": paper.pdf_url,
            "published_date": paper.published_date.isoformat(),
            # The schema allows a Paper without them, though the Ingestion always sets both. Its entry then has none.
            "created_at": paper.created_at.isoformat() if paper.created_at else None,
            "updated_at": paper.updated_at.isoformat() if paper.updated_at else None,
        }


def make_paper_indexer(database: BaseDatabase, settings: Settings | None = None) -> PaperIndexer:
    """The indexing of the Papers the database holds into the Index the settings name, with their maximum Content size."""
    settings = settings or get_settings()
    return PaperIndexer(
        database,
        OpenSearchClient(host=settings.opensearch.host, settings=settings),
        max_content_size=settings.opensearch.max_text_size,
    )
