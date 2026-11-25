import logging

from sqlalchemy import text

from .common import get_cached_services

logger = logging.getLogger(__name__)


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
