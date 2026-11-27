import logging
from datetime import datetime

from .common import run_day

logger = logging.getLogger(__name__)


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

        opensearch_results = context["task_instance"].xcom_pull(task_ids="index_papers_hybrid")
        if not opensearch_results:
            # The report runs once the indexing has succeeded or failed. A failed indexing returns nothing, but records it.
            opensearch_results = context["task_instance"].xcom_pull(task_ids="index_papers_hybrid", key="indexing_failure")

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
                "chunks_indexed": opensearch_results.get("chunks_indexed", 0) if opensearch_results else 0,
                "embeddings_indexed": opensearch_results.get("embeddings_indexed", 0) if opensearch_results else 0,
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
        logger.info(f"Chunks indexed: {report['opensearch']['chunks_indexed']}")
        logger.info(f"Embeddings indexed: {report['opensearch']['embeddings_indexed']}")
        logger.info(f"Total in index: {report['opensearch']['total_documents_in_index']}")
        logger.info(f"OpenSearch status: {report['opensearch']['status']}")
        logger.info("=== END REPORT ===")

        return report

    except Exception as e:
        error_msg = f"Report generation failed: {e!s}"
        logger.error(error_msg)
        raise Exception(error_msg) from e
