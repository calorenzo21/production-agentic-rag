import logging
from collections.abc import Callable

from sqlalchemy import text
from src.exceptions import OpenSearchException

from .common import get_cached_services

logger = logging.getLogger(__name__)


def setup_environment():
    """Setup environment and verify dependencies.

    Creates hybrid search index with RRF pipeline.
    """
    logger.info("Setting up environment for arXiv paper ingestion")

    try:
        # Get cached services (initialized once)
        arxiv_client, _pdf_parser, database, _metadata_fetcher, opensearch_client = get_cached_services()

        # Test database connection
        with database.get_session() as session:
            session.execute(text("SELECT 1"))
            logger.info("Database connection verified")

        # Test OpenSearch connection and create the Index and the RRF pipeline if needed. Unlike the course's, the setup only
        # warns when OpenSearch, the Index or the pipeline is unavailable, so that the day's Ingestion runs anyway (README.md).
        if opensearch_client.health_check():
            logger.info("OpenSearch connection verified")
            # The client reports one it could not create, after logging why, as not created, like one that already exists,
            # so the setup looks up each it did not create.
            setup_results = opensearch_client.setup_indices(force=False)
            if setup_results.get("hybrid_index"):
                logger.info("Hybrid search index created with vector support")
            elif _found(opensearch_client.index_exists):
                logger.info("Hybrid search index already exists")
            else:
                logger.warning("Hybrid search index unavailable, indexing will fail")

            if setup_results.get("rrf_pipeline"):
                logger.info("RRF pipeline created successfully")
            elif _found(opensearch_client.rrf_pipeline_exists):
                logger.info("RRF pipeline already exists")
            else:
                logger.warning("RRF pipeline unavailable, searches by meaning will fail")

            logger.info("Hybrid search setup completed")
        else:
            logger.warning("OpenSearch not healthy, indexing will fail")

        logger.info(f"arXiv client ready: {arxiv_client.base_url}")
        logger.info("PDF parser service ready (Docling models cached)")

        return {"status": "success", "message": "Environment setup completed"}

    except Exception as e:
        error_msg = f"Environment setup failed: {e!s}"
        logger.error(error_msg)
        raise Exception(error_msg) from e


def _found(lookup: Callable[[], bool]) -> bool:
    """What an OpenSearch lookup finds, taking an OpenSearch that cannot tell for one that finds nothing."""
    try:
        return lookup()
    except OpenSearchException:
        return False
