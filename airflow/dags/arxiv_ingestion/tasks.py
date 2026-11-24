import asyncio
import logging
import time
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Any

from sqlalchemy import text
from src.db.factory import make_database
from src.exceptions import PapersNotIndexed
from src.services.arxiv.factory import make_arxiv_client
from src.services.metadata_fetcher import make_metadata_fetcher
from src.services.opensearch.factory import make_opensearch_client
from src.services.paper_indexer import make_paper_indexer
from src.services.pdf_parser.factory import make_pdf_parser_service

logger = logging.getLogger(__name__)

# How long the cleanup keeps a downloaded PDF.
PDF_RETENTION = timedelta(days=30)

# How far back from its day a run ingests. arXiv lists a day's Papers only once it announces them: at 20:00 ET from
# Sunday to Thursday, each announcement covering the submissions up to 14:00 ET, Friday's on Sunday. A Thursday's
# Papers submitted after 14:00 ET are announced on Sunday evening and a Friday's on Monday evening, so both are complete
# by 06:00 UTC four days later. Four days back, every day is complete, arXiv's holidays aside.
ANNOUNCEMENT_LAG = timedelta(days=4)


@lru_cache(maxsize=1)
def get_cached_services() -> tuple[Any, Any, Any, Any, Any]:
    """
    Get cached service instances using lru_cache for automatic memoization.

    Returns:
        Tuple of (arxiv_client, pdf_parser, database, metadata_fetcher, opensearch_client)
    """
    logger.info("Initializing services (cached with lru_cache)")

    # Initialize core services
    arxiv_client = make_arxiv_client()
    pdf_parser = make_pdf_parser_service()
    database = make_database()
    opensearch_client = make_opensearch_client()

    # Create metadata fetcher with dependencies. Unlike the course's, it gets no OpenSearch client, as it does not index.
    metadata_fetcher = make_metadata_fetcher(arxiv_client, pdf_parser)

    logger.info("All services initialized and cached with lru_cache")
    return arxiv_client, pdf_parser, database, metadata_fetcher, opensearch_client


def run_day(*, logical_date: datetime | None, run_after: datetime) -> date:
    """
    A run's day: its logical date's, which is `ds`. A manual run without a logical date, which Airflow 3 allows and
    which has no `ds`, runs on the day it was triggered.
    """
    return (logical_date or run_after).date()


def day_to_ingest(*, logical_date: datetime | None, run_after: datetime) -> date:
    """The day a run ingests: ANNOUNCEMENT_LAG before its run day, when arXiv has announced all that day's Papers."""
    return run_day(logical_date=logical_date, run_after=run_after) - ANNOUNCEMENT_LAG


async def run_paper_ingestion_pipeline(target_date: str, process_pdfs: bool = True) -> dict:
    """
    Async wrapper for the paper ingestion pipeline.

    Args:
        target_date: Date to fetch papers for (YYYYMMDD format)
        process_pdfs: Whether to process PDFs

    Returns:
        Dictionary with processing results
    """
    _arxiv_client, _pdf_parser, database, metadata_fetcher, _opensearch_client = get_cached_services()

    with database.get_session() as session:
        return await metadata_fetcher.fetch_and_process_papers(
            from_date=target_date,
            to_date=target_date,
            process_pdfs=process_pdfs,
            store_to_db=True,
            db_session=session,
        )


def setup_environment():
    """Setup environment and verify dependencies."""
    logger.info("Setting up environment for arXiv paper ingestion")

    try:
        # Get cached services (initialized once)
        arxiv_client, _pdf_parser, database, _metadata_fetcher, opensearch_client = get_cached_services()

        # Test database connection
        with database.get_session() as session:
            session.execute(text("SELECT 1"))
            logger.info("Database connection verified")

        # Test OpenSearch connection and create index if needed
        if opensearch_client.health_check():
            logger.info("OpenSearch connection verified")
            # Ensure index exists
            if opensearch_client.create_index(force=False):
                logger.info("OpenSearch index created")
            else:
                logger.info("OpenSearch index already exists")
        else:
            logger.warning("OpenSearch not healthy, indexing will fail")

        logger.info(f"arXiv client ready: {arxiv_client.base_url}")
        logger.info("PDF parser service ready (Docling models cached)")

        return {"status": "success", "message": "Environment setup completed"}

    except Exception as e:
        error_msg = f"Environment setup failed: {e!s}"
        logger.error(error_msg)
        raise Exception(error_msg) from e


def fetch_daily_papers(**context):
    """
    Ingest the Papers submitted ANNOUNCEMENT_LAG before the run's day, and store them to PostgreSQL only. Its results
    list the arXiv IDs of the Papers stored, which the indexing task indexes.
    """
    logger.info("Starting daily arXiv paper fetch")

    try:
        dag_run = context["dag_run"]
        target_date = day_to_ingest(logical_date=dag_run.logical_date, run_after=dag_run.run_after).strftime("%Y%m%d")
        logger.info(f"Fetching papers for date: {target_date}")
        results = asyncio.run(run_paper_ingestion_pipeline(target_date=target_date, process_pdfs=True))

        if results.get("papers_fetched", 0) == 0:
            logger.warning(f"No papers found for date {target_date}")

        # Store results for downstream tasks
        context["task_instance"].xcom_push(key="fetch_results", value=results)

        return results

    except Exception as e:
        error_msg = f"Daily paper fetch failed: {e!s}"
        logger.error(error_msg)
        raise Exception(error_msg) from e


def index_papers_to_opensearch(**context):
    """
    Index the Papers the day's Ingestion stored, from PostgreSQL to OpenSearch.

    This is the single dedicated task for OpenSearch indexing. fetch_daily_papers passes on the arXiv IDs of the Papers
    its Ingestion stored, both new ones and those updated to a new Version, and the indexing module indexes exactly those
    Papers. The course indexes instead the Papers whose rows were created today, which misses a Paper the upsert updated
    to a new Version, as its row was created with an earlier one (ADR 0004).

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
        # Only the indexing module's failure tells how many Papers it indexed first.
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


def generate_daily_report(**context):
    """
    Generate a daily processing report.

    This function:
    1. Collects results from all tasks
    2. Generates summary statistics
    3. Logs the daily report
    """
    logger.info("Generating daily processing report")

    try:
        fetch_results = context["task_instance"].xcom_pull(task_ids="fetch_daily_papers", key="fetch_results")

        opensearch_results = context["task_instance"].xcom_pull(task_ids="index_papers_to_opensearch")
        if not opensearch_results:
            # The report runs once the indexing has succeeded or failed. A failed indexing returns nothing, but records it.
            opensearch_results = context["task_instance"].xcom_pull(task_ids="index_papers_to_opensearch", key="indexing_failure")

        dag_run = context["dag_run"]

        report = {
            "date": run_day(logical_date=dag_run.logical_date, run_after=dag_run.run_after).isoformat(),
            "execution_time": datetime.now().isoformat(),
            "papers": {
                "fetched": fetch_results.get("papers_fetched", 0) if fetch_results else 0,
                "pdfs_downloaded": fetch_results.get("pdfs_downloaded", 0) if fetch_results else 0,
                "pdfs_parsed": fetch_results.get("pdfs_parsed", 0) if fetch_results else 0,
                "stored": fetch_results.get("papers_stored", 0) if fetch_results else 0,
            },
            "processing": {
                "processing_time_seconds": fetch_results.get("processing_time", 0) if fetch_results else 0,
                "errors": len(fetch_results.get("errors", [])) if fetch_results else 0,
                "pdf_failures_skipped": len(fetch_results.get("errors", [])) if fetch_results else 0,
            },
            "opensearch": {
                "papers_indexed": opensearch_results.get("papers_indexed", 0) if opensearch_results else 0,
                "indexing_failures": opensearch_results.get("indexing_failures", 0) if opensearch_results else 0,
                "total_documents_in_index": opensearch_results.get("total_documents_in_index", 0) if opensearch_results else 0,
                "status": opensearch_results.get("status", "unknown") if opensearch_results else "unknown",
            },
        }

        logger.info("=== DAILY ARXIV PROCESSING REPORT ===")
        logger.info(f"Date: {report['date']}")
        logger.info(f"Papers fetched: {report['papers']['fetched']}")
        logger.info(f"PDFs downloaded: {report['papers']['pdfs_downloaded']}")
        logger.info(f"PDFs parsed: {report['papers']['pdfs_parsed']}")
        logger.info(f"Papers stored: {report['papers']['stored']}")
        logger.info(f"Processing time: {report['processing']['processing_time_seconds']:.1f}s")
        logger.info(f"PDF failures (oversized docs skipped): {report['processing']['pdf_failures_skipped']}")
        logger.info(f"OpenSearch indexed: {report['opensearch']['papers_indexed']}")
        logger.info(f"OpenSearch failures: {report['opensearch']['indexing_failures']}")
        logger.info(f"Total in index: {report['opensearch']['total_documents_in_index']}")
        logger.info(f"OpenSearch status: {report['opensearch']['status']}")
        logger.info("=== END REPORT ===")

        return report

    except Exception as e:
        error_msg = f"Report generation failed: {e!s}"
        logger.error(error_msg)
        raise Exception(error_msg) from e


def fail_the_run() -> None:
    """Fail the run. The DAG's watcher calls it once another task has failed."""
    raise RuntimeError("A task of this run failed, so the run fails too")


def cleanup_temp_files() -> dict[str, int]:
    """
    Delete the PDFs older than PDF_RETENTION from the folder the arXiv client downloads them into, so it does not grow
    with every run. Newer PDFs stay, as the client reuses a PDF it already has instead of downloading it again under
    arXiv's rate limit.

    The folder is the arXiv client's, from ARXIV__PDF_CACHE_DIR. A relative one resolves against the working directory,
    which this task shares with the task that downloads the PDFs.
    """
    pdf_cache_dir = make_arxiv_client().pdf_cache_dir
    cutoff = time.time() - PDF_RETENTION.total_seconds()
    logger.info(f"Deleting PDFs older than {PDF_RETENTION.days} days from {pdf_cache_dir.resolve()}")

    deleted = 0
    for pdf in pdf_cache_dir.glob("*.pdf"):
        if pdf.stat().st_mtime < cutoff:
            pdf.unlink()
            deleted += 1

    logger.info(f"Deleted {deleted} PDFs")
    return {"pdfs_deleted": deleted}
