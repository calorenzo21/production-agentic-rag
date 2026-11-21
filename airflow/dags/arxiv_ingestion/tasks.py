import asyncio
import logging
import time
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Any

from sqlalchemy import text
from src.db.factory import make_database
from src.services.arxiv.factory import make_arxiv_client
from src.services.metadata_fetcher import make_metadata_fetcher
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
def get_cached_services() -> tuple[Any, Any, Any, Any]:
    """
    Get cached service instances using lru_cache for automatic memoization.

    Returns:
        Tuple of (arxiv_client, pdf_parser, database, metadata_fetcher)
    """
    logger.info("Initializing services (cached with lru_cache)")

    # Initialize core services
    arxiv_client = make_arxiv_client()
    pdf_parser = make_pdf_parser_service()
    database = make_database()

    # Create metadata fetcher with dependencies
    metadata_fetcher = make_metadata_fetcher(arxiv_client, pdf_parser)

    logger.info("All services initialized and cached with lru_cache")
    return arxiv_client, pdf_parser, database, metadata_fetcher


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
    _arxiv_client, _pdf_parser, database, metadata_fetcher = get_cached_services()

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
        arxiv_client, _pdf_parser, database, _metadata_fetcher = get_cached_services()

        # Test database connection
        with database.get_session() as session:
            session.execute(text("SELECT 1"))
            logger.info("Database connection verified")

        logger.info(f"arXiv client ready: {arxiv_client.base_url}")
        logger.info("PDF parser service ready (Docling models cached)")

        return {"status": "success", "message": "Environment setup completed"}

    except Exception as e:
        error_msg = f"Environment setup failed: {e!s}"
        logger.error(error_msg)
        raise Exception(error_msg) from e


def fetch_daily_papers(**context):
    """
    Ingest the Papers submitted ANNOUNCEMENT_LAG before the run's day.

    This function:
    1. Works out the day to ingest from the run
    2. Fetches papers using the MetadataFetcher
    3. Returns processing statistics
    """
    logger.info("Starting daily arXiv paper fetch")

    try:
        dag_run = context["dag_run"]
        target_date = day_to_ingest(logical_date=dag_run.logical_date, run_after=dag_run.run_after).strftime("%Y%m%d")

        logger.info(f"Fetching papers for date: {target_date}")

        # Execute paper ingestion pipeline
        results = asyncio.run(run_paper_ingestion_pipeline(target_date=target_date, process_pdfs=True))
        logger.info(f"Daily paper fetch completed: {results}")

        # Store results for downstream tasks
        context["task_instance"].xcom_push(key="fetch_results", value=results)

        return results

    except Exception as e:
        error_msg = f"Daily paper fetch failed: {e!s}"
        logger.error(error_msg)
        raise Exception(error_msg) from e


def process_failed_pdfs(**context):
    """
    Retry processing of PDFs that failed in the main fetch task.

    This function:
    1. Gets failed PDF list from the main task
    2. Retries processing with different settings
    3. Reports final success/failure statistics
    """
    logger.info("Processing failed PDFs")

    try:
        fetch_results = context["task_instance"].xcom_pull(task_ids="fetch_daily_papers", key="fetch_results")

        if not fetch_results or not fetch_results.get("errors"):
            logger.info("No failed PDFs to retry")
            return {"status": "skipped", "message": "No failures to retry"}

        logger.info(f"Found {len(fetch_results['errors'])} errors to investigate")

        for error in fetch_results["errors"]:
            # TODO: Implement retry logic
            logger.warning(f"Error to investigate: {error}")

        return {
            "status": "analyzed",
            "errors_logged": len(fetch_results["errors"]),
            "message": "Errors logged for investigation",
        }

    except Exception as e:
        error_msg = f"Failed PDF processing error: {e!s}"
        logger.error(error_msg)
        raise Exception(error_msg) from e


def create_opensearch_placeholders(**context):
    """
    Create placeholder entries for OpenSearch indexing.

    This is a Week 2 placeholder - in Week 3+ this will:
    1. Get successfully stored papers
    2. Create placeholder OpenSearch documents
    3. Prepare for actual indexing pipeline
    """
    logger.info("Creating OpenSearch placeholders (Week 2)")

    try:
        fetch_results = context["task_instance"].xcom_pull(task_ids="fetch_daily_papers", key="fetch_results")

        if not fetch_results:
            logger.warning("No fetch results available for OpenSearch placeholders")
            return {"status": "skipped", "message": "No papers to process"}

        papers_stored = fetch_results.get("papers_stored", 0)

        logger.info(f"Creating placeholders for {papers_stored} papers")

        # Week 2: Just log what would be indexed
        # Week 3+: Actually create OpenSearch documents
        placeholder_results = {
            "status": "placeholder",
            "papers_ready_for_indexing": papers_stored,
            "message": f"Week 2: {papers_stored} papers ready for future OpenSearch indexing",
        }

        logger.info(f"OpenSearch placeholders: {placeholder_results}")

        return placeholder_results

    except Exception as e:
        error_msg = f"OpenSearch placeholder creation failed: {e!s}"
        logger.error(error_msg)
        raise Exception(error_msg) from e


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

        failed_pdf_results = context["task_instance"].xcom_pull(task_ids="process_failed_pdfs")

        opensearch_results = context["task_instance"].xcom_pull(task_ids="create_opensearch_placeholders")

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
                "failed_pdf_retries": failed_pdf_results.get("errors_logged", 0) if failed_pdf_results else 0,
            },
            "opensearch": {
                "placeholders_created": opensearch_results.get("papers_ready_for_indexing", 0) if opensearch_results else 0,
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
        logger.info(f"Errors encountered: {report['processing']['errors']}")
        logger.info(f"OpenSearch placeholders: {report['opensearch']['placeholders_created']}")
        logger.info("=== END REPORT ===")

        return report

    except Exception as e:
        error_msg = f"Report generation failed: {e!s}"
        logger.error(error_msg)
        raise Exception(error_msg) from e


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
