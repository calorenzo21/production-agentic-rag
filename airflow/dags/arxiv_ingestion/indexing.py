import asyncio
import logging

from src.exceptions import PapersNotIndexed
from src.services.paper_indexer import make_paper_indexer

from .common import get_cached_services

logger = logging.getLogger(__name__)


def index_papers_hybrid(**context):
    """
    Index the Papers the day's Ingestion stored, from PostgreSQL to OpenSearch, as Chunks with their embeddings, for
    hybrid search.

    fetch_daily_papers passes on the arXiv IDs of the Papers its Ingestion stored, both new ones and those updated to a
    new Version, and the indexing module, src/services/paper_indexer.py, splits exactly those Papers into Chunks, embeds
    them with Jina AI and indexes them. The course indexes instead the most recently created rows, as many as the
    Ingestion stored, which misses a Paper the upsert updated to a new Version, as its row was created with an earlier
    one (ADR 0004).

    The course's task stays green whether OpenSearch is down or a Paper cannot be indexed, as its indexer only counts the
    Paper among its errors. This one fails when the indexing does, so that Airflow retries it, after recording the
    failure for the daily report, which runs anyway.
    """
    fetch_results = context["task_instance"].xcom_pull(task_ids="fetch_daily_papers", key="fetch_results")

    if not fetch_results:
        logger.warning("No fetch results available for OpenSearch indexing")
        return {"status": "skipped", "message": "No papers to process"}

    stored_arxiv_ids = fetch_results.get("stored_arxiv_ids", [])

    if not stored_arxiv_ids:
        logger.info("No papers were stored, skipping OpenSearch indexing")
        return {"status": "skipped", "papers_indexed": 0, "message": "No papers available for indexing"}

    logger.info(f"Indexing {len(stored_arxiv_ids)} papers for hybrid search")

    try:
        # Get services
        _arxiv_client, _pdf_parser, database, _metadata_fetcher, opensearch_client = get_cached_services()

        # The indexing embeds the Chunks with Jina AI's async client, so it runs on an event loop, as the course's does.
        counts = asyncio.run(make_paper_indexer(database).index_papers(stored_arxiv_ids))
    except Exception as e:
        error_msg = f"OpenSearch indexing failed: {e!s}"
        logger.error(error_msg)
        # A failed task returns nothing, so the failure goes to the daily report, which runs anyway, through XCom.
        failure = {
            "status": "failed",
            "papers_indexed": "unknown",
            "indexing_failures": "unknown",
            "chunks_indexed": "unknown",
            "embeddings_indexed": "unknown",
            "total_documents_in_index": "unknown",
            "message": error_msg,
        }
        # Only the failure of the indexing module, src/services/paper_indexer.py, tells how many Papers it indexed first.
        if isinstance(e, PapersNotIndexed):
            failure |= {"papers_indexed": e.papers_indexed, "indexing_failures": len(stored_arxiv_ids) - e.papers_indexed}
        context["task_instance"].xcom_push(key="indexing_failure", value=failure)
        raise

    indexed_count = counts["papers_indexed"]
    failed_count = counts["papers_to_index"] - indexed_count
    chunks_count = counts["chunks_indexed"]

    # Get final index stats
    try:
        final_stats = opensearch_client.get_index_stats()
        total_docs = final_stats.get("document_count", 0) if final_stats else 0
    except Exception:
        total_docs = "unknown"

    indexing_results = {
        "status": "completed",
        "papers_indexed": indexed_count,
        "indexing_failures": failed_count,
        "chunks_indexed": chunks_count,
        # The indexing module indexes each Chunk with its embedding, so it indexed as many embeddings as Chunks.
        "embeddings_indexed": chunks_count,
        "total_documents_in_index": total_docs,
        "message": f"Indexed {indexed_count} papers as {chunks_count} chunks, {failed_count} failures",
    }

    # Log detailed summary
    logger.info("=" * 60)
    logger.info("OpenSearch Indexing Summary:")
    logger.info(f"  Papers stored by the Ingestion: {counts['papers_to_index']}")
    logger.info(f"  Papers indexed: {indexed_count}")
    logger.info(f"  Indexing failures: {failed_count}")
    logger.info(f"  Chunks indexed, each with its embedding: {chunks_count}")
    logger.info(f"  Total docs in index: {total_docs}")
    logger.info("=" * 60)

    return indexing_results
