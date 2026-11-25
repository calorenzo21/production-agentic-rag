import asyncio
import logging
from datetime import date, datetime, timedelta

from .common import get_cached_services, run_day

logger = logging.getLogger(__name__)

# How far back from its day a run ingests. arXiv lists a day's Papers only once it announces them: at 20:00 ET from
# Sunday to Thursday, each announcement covering the submissions up to 14:00 ET, Friday's on Sunday. A Thursday's
# Papers submitted after 14:00 ET are announced on Sunday evening and a Friday's on Monday evening, so both are complete
# by 06:00 UTC four days later. Four days back, every day is complete, arXiv's holidays aside.
ANNOUNCEMENT_LAG = timedelta(days=4)


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
