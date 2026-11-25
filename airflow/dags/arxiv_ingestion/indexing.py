import logging

from src.exceptions import PapersNotIndexed
from src.services.paper_indexer import make_paper_indexer

from .common import get_cached_services

logger = logging.getLogger(__name__)


def index_papers_to_opensearch(**context):
    """
    Index the Papers the day's Ingestion stored, from PostgreSQL to OpenSearch.

    This is the single dedicated task for OpenSearch indexing. fetch_daily_papers passes on the arXiv IDs of the Papers
    its Ingestion stored, both new ones and those updated to a new Version, and the indexing module,
    src/services/paper_indexer.py, indexes exactly those Papers. The course indexes instead the Papers whose rows were
    created today, which misses a Paper the upsert updated to a new Version, as its row was created with an earlier one
    (ADR 0004).

    The course's task returns a failed or error status and stays green, whether OpenSearch is down or a Paper cannot be
    indexed. This one fails when the indexing does, so that Airflow retries it, after recording the failure for the
    daily report, which runs anyway.
    """
    logger.info("Starting OpenSearch indexing (Week 3 - Dedicated Task)")

    fetch_results = context["task_instance"].xcom_pull(task_ids="fetch_daily_papers", key="fetch_results")

    if not fetch_results:
        logger.warning("No fetch results available for OpenSearch indexing")
        return {"status": "skipped", "message": "No papers to process"}

    stored_arxiv_ids = fetch_results.get("stored_arxiv_ids", [])

    if not stored_arxiv_ids:
        logger.info("No papers were stored, skipping OpenSearch indexing")
        return {"status": "skipped", "papers_indexed": 0, "message": "No papers available for indexing"}

    logger.info(f"Processing {len(stored_arxiv_ids)} papers for OpenSearch indexing")

    # Unlike the course's, it does not check OpenSearch's health first, as the indexing fails when OpenSearch does not answer.
    try:
        # Get services
        _arxiv_client, _pdf_parser, database, _metadata_fetcher, opensearch_client = get_cached_services()

        counts = make_paper_indexer(database).index_papers(stored_arxiv_ids)
    except Exception as e:
        error_msg = f"OpenSearch indexing failed: {e!s}"
        logger.error(error_msg)
        # A failed task returns nothing, so the failure goes to the daily report, which runs anyway, through XCom.
        failure = {
            "status": "failed",
            "papers_indexed": "unknown",
            "indexing_failures": "unknown",
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
        "total_documents_in_index": total_docs,
        "message": f"Indexed {indexed_count} papers, {failed_count} failures",
    }

    # Log detailed summary
    logger.info("=" * 60)
    logger.info("OpenSearch Indexing Summary:")
    logger.info(f"  Papers stored by the Ingestion: {counts['papers_to_index']}")
    logger.info(f"  Papers indexed: {indexed_count}")
    logger.info(f"  Indexing failures: {failed_count}")
    logger.info(f"  Total docs in index: {total_docs}")
    logger.info("=" * 60)

    return indexing_results
