"""
The indexing of Papers into the Index, as Chunks. The course indexes them in the DAG's task, through its hybrid indexer,
which its own factory builds. Here this module alone indexes them, with the course's chunker and Jina client, so that a
Paper is indexed the same way whoever asks.
"""

import logging
from collections.abc import Callable
from functools import partial
from typing import Any

from sqlalchemy import Row, Select, select
from src.config import Settings, get_settings
from src.db.interfaces.base import BaseDatabase
from src.exceptions import OpenSearchException, PapersNotIndexed
from src.models.paper import Paper
from src.schemas.indexing.models import TextChunk
from src.services.embeddings.factory import make_embeddings_service
from src.services.embeddings.jina_client import JinaEmbeddingsClient
from src.services.indexing.text_chunker import TextChunker
from src.services.opensearch.client import OpenSearchClient

logger = logging.getLogger(__name__)

# What the chunker and a Paper's Chunks need, read without its references, which can be as large as its text.
PAPER_COLUMNS = (
    Paper.id,
    Paper.arxiv_id,
    Paper.title,
    Paper.authors,
    Paper.abstract,
    Paper.categories,
    Paper.raw_text,
    Paper.sections,
    Paper.pdf_url,
    Paper.published_date,
)

# How many Papers to read from PostgreSQL at a time, so that indexing never holds every Paper's Content at once.
BATCH_SIZE = 100


class PaperIndexer:
    """Indexes the Papers the curator holds into the Index, each as its Chunks, embedded with Jina AI."""

    def __init__(
        self,
        database: BaseDatabase,
        opensearch_client: OpenSearchClient,
        chunker: TextChunker,
        make_jina_client: Callable[[], JinaEmbeddingsClient],
    ) -> None:
        self.database = database
        self.opensearch_client = opensearch_client
        self.chunker = chunker
        # Each indexing makes a Jina client of its own, which it closes when it ends.
        self.make_jina_client = make_jina_client

    async def rebuild_index(self) -> dict[str, int]:
        """
        Delete the Week 3 index, and recreate the Index with the course's mapping, and its RRF pipeline, then index every
        Paper the curator holds, and count the Papers held, and the Papers and Chunks indexed. Until it ends, Searches find
        fewer Papers, as the Index starts empty.

        Raises PapersNotIndexed before indexing any Paper when OpenSearch does not delete the Week 3 index or recreate the
        Index or its RRF pipeline, as when it does not answer. Otherwise it indexes every Paper it can, then raises it
        naming the others.
        """
        try:
            # First, so that a failure leaves the Index as it was.
            self.opensearch_client.delete_week3_index()
        except OpenSearchException as e:
            raise PapersNotIndexed(f"Could not delete the Week 3 index, so indexed no Paper: {e}", papers_indexed=0) from e
        recreated = self.opensearch_client.setup_indices(force=True)
        if not recreated["hybrid_index"]:
            # The client logs why. Whatever Index is left may have another mapping, or none, which indexing would create.
            raise PapersNotIndexed(
                f"Could not recreate the Index {self.opensearch_client.index_name}, so indexed no Paper", papers_indexed=0
            )
        if not recreated["rrf_pipeline"]:
            # The client logs why. The client deleted it first, as the course does, so a Search by meaning answers 503
            # until a pipeline exists again, which the API's next startup and the DAG's next setup create.
            raise PapersNotIndexed(
                f"Could not recreate the RRF pipeline {self.opensearch_client.settings.opensearch.rrf_pipeline_name}, so "
                "indexed no Paper",
                papers_indexed=0,
            )
        held, chunks_indexed, not_indexed = await self._index(select(*PAPER_COLUMNS), replace_existing=False)
        indexed = held - len(not_indexed)
        self._log_or_fail(
            f"Indexed {indexed} of the {held} Papers the curator holds into {self.opensearch_client.index_name}, as "
            f"{chunks_indexed} Chunks",
            indexed,
            not_indexed,
        )
        return {"papers_held": held, "papers_indexed": indexed, "chunks_indexed": chunks_indexed}

    async def index_papers(self, arxiv_ids: list[str]) -> dict[str, int]:
        """
        Index the Papers with these arXiv IDs, without their Versions, into the Index, and count the Papers to index, and
        the Papers and Chunks indexed. The Index must exist, as the DAG's setup task and the API's startup create it. The
        Chunks of a Paper the Index already holds, as one updated to a new Version, are replaced, and whatever fails, its
        previous Chunks stay until its new ones are written.

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
            # OpenSearch would create it for the first Chunk, with a mapping it guesses, where k-NN queries fail and the
            # Category filter matches nothing. Rebuilding the Index creates it with the course's mapping.
            raise PapersNotIndexed(
                f"The Index {self.opensearch_client.index_name} does not exist, so indexed none of the {len(arxiv_ids)} "
                "Papers to index",
                papers_indexed=0,
            )
        statement = select(*PAPER_COLUMNS).where(Paper.arxiv_id.in_(arxiv_ids))
        found, chunks_indexed, not_indexed = await self._index(statement, replace_existing=True)
        indexed = found - len(not_indexed)
        self._log_or_fail(
            f"Indexed {indexed} of the {len(arxiv_ids)} Papers to index into {self.opensearch_client.index_name}, as "
            f"{chunks_indexed} Chunks",
            indexed,
            not_indexed,
        )
        return {"papers_to_index": len(arxiv_ids), "papers_indexed": indexed, "chunks_indexed": chunks_indexed}

    @staticmethod
    def _log_or_fail(summary: str, indexed: int, not_indexed: list[str]) -> None:
        """Log the summary of an indexing, or raise it as PapersNotIndexed when the indexing left Papers out, naming them."""
        if not_indexed:
            raise PapersNotIndexed(f"{summary}, but could not index {', '.join(not_indexed)}", papers_indexed=indexed)
        logger.info(summary)

    async def _index(self, statement: Select[Any], *, replace_existing: bool) -> tuple[int, int, list[str]]:
        """
        Index the Papers the statement selects, reading BATCH_SIZE at a time, and count those selected and the Chunks
        indexed. One Paper that cannot be indexed does not stop the others, so it also returns the arXiv IDs of those it
        could not index. Neither does an OpenSearch that stops answering midway: each Paper left then fails on its own,
        after the client's timeout. Nor does Jina, when it keeps failing to embed a Paper through the Jina client's
        retries.
        """
        selected = 0
        chunks_indexed = 0
        not_indexed: list[str] = []
        # The course never closes its Jina client (README.md). This one is closed when the indexing ends, however it ends.
        async with self.make_jina_client() as jina_client:
            with self.database.get_session() as session:
                for paper in session.execute(statement.execution_options(yield_per=BATCH_SIZE)):
                    selected += 1
                    # The course logs a Paper it cannot index and goes on as if it had (README.md).
                    try:
                        chunks_written, failure = await self._index_paper(paper, jina_client, replace_existing=replace_existing)
                    except Exception as e:
                        logger.error(f"Could not index {paper.arxiv_id}: {e}")
                        not_indexed.append(paper.arxiv_id)
                        continue
                    # Including those of a Paper it could not wholly index, as the Index holds them.
                    chunks_indexed += chunks_written
                    if failure is not None:
                        logger.error(f"Could not index {paper.arxiv_id}: {failure}")
                        not_indexed.append(paper.arxiv_id)
        return selected, chunks_indexed, not_indexed

    async def _index_paper(
        self, paper: Row[Any], jina_client: JinaEmbeddingsClient, *, replace_existing: bool
    ) -> tuple[int, str | None]:
        """
        Index a Paper as its Chunks, and count those OpenSearch indexed, with why it could not wholly index the Paper,
        when it could not: OpenSearch rejected some of its Chunks, or failed to delete its previous ones left over. Raises
        when it cannot index any, as when the Paper cannot be chunked or embedded, or OpenSearch does not answer.

        To replace the Chunks the Index holds of the Paper, as when it has a new Version, it writes each new Chunk over
        the one at its position, and once OpenSearch has indexed them all, it deletes the Paper's other Chunks: those
        beyond their count, and any an Index built before each Chunk had an ID holds under a random one. So whatever
        fails, the Paper's previous Chunks stay until its new ones are written. The course deletes them all first, before
        even embedding the new ones, and goes on when it cannot (README.md). A rebuild has none to replace, as it starts
        from an empty Index.
        """
        chunks = self.chunker.chunk_paper(
            title=paper.title,
            abstract=paper.abstract,
            full_text=paper.raw_text or "",
            arxiv_id=paper.arxiv_id,
            paper_id=str(paper.id),
            sections=paper.sections,
        )
        embeddings = await jina_client.embed_passages(texts=[chunk.text for chunk in chunks], batch_size=50)
        entries = [self._entry(paper, chunk, embedding) for chunk, embedding in zip(chunks, embeddings, strict=True)]
        results = self.opensearch_client.bulk_index_chunks(entries)
        if results["failed"]:
            return results["success"], f"OpenSearch rejected {results['failed']} of its Chunks"
        if replace_existing:
            try:
                self.opensearch_client.delete_paper_chunks(paper.arxiv_id, keeping=[entry["chunk_id"] for entry in entries])
            except OpenSearchException as e:
                return results["success"], f"OpenSearch failed to delete its previous Chunks left over: {e}"
        return results["success"], None

    @staticmethod
    def _entry(paper: Row[Any], chunk: TextChunk, embedding: list[float]) -> dict[str, Any]:
        """
        A Chunk's entry in the Index: the course's, with its ID, its Paper's PDF link, and its authors as a list. The
        Index holds it under that ID.
        """
        return {
            # Its Paper's arXiv ID, without its Version, and its position, so that indexing the Paper again writes each
            # Chunk over the one at its position. The course lets OpenSearch pick a random ID, and never writes this field
            # (README.md).
            "chunk_id": f"{chunk.arxiv_id}_{chunk.metadata.chunk_index}",
            "arxiv_id": chunk.arxiv_id,
            "paper_id": chunk.paper_id,
            "chunk_index": chunk.metadata.chunk_index,
            "chunk_text": chunk.text,
            "chunk_word_count": chunk.metadata.word_count,
            "start_char": chunk.metadata.start_char,
            "end_char": chunk.metadata.end_char,
            "section_title": chunk.metadata.section_title,
            "embedding": embedding,
            "embedding_model": "jina-embeddings-v3",
            # Denormalized paper metadata for efficient search
            "title": paper.title,
            # The course's joins them into one string, which a Search returns as it is (README.md).
            "authors": paper.authors,
            "abstract": paper.abstract,
            "categories": paper.categories,
            "published_date": paper.published_date.isoformat(),
            "pdf_url": paper.pdf_url,
        }


def make_paper_indexer(database: BaseDatabase, settings: Settings | None = None) -> PaperIndexer:
    """
    The indexing of the Papers the database holds into the Index the settings name, chunked as they say, and embedded
    with Jina AI with the key they hold.
    """
    settings = settings or get_settings()
    chunker = TextChunker(
        chunk_size=settings.chunking.chunk_size,
        overlap_size=settings.chunking.overlap_size,
        min_chunk_size=settings.chunking.min_chunk_size,
        section_based=settings.chunking.section_based,
    )
    return PaperIndexer(
        database,
        OpenSearchClient(host=settings.opensearch.host, settings=settings),
        chunker,
        partial(make_embeddings_service, settings),
    )
