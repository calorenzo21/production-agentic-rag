import logging
from datetime import datetime
from typing import Any

from dateutil import parser as date_parser
from sqlalchemy.orm import Session
from src.exceptions import PipelineException
from src.repositories.paper import PaperRepository
from src.schemas.arxiv.paper import ArxivPaper
from src.schemas.paper import PaperCreate
from src.services.arxiv.client import ArxivClient

logger = logging.getLogger(__name__)


class MetadataFetcher:
    """
    Service for fetching arXiv papers and storing them in PostgreSQL.

    This service orchestrates the pipeline:
    1. Fetch paper metadata from arXiv API
    2. Store paper metadata in PostgreSQL
    """

    def __init__(self, arxiv_client: ArxivClient):
        """
        Initialize metadata fetcher.

        Args:
            arxiv_client: ArxivClient instance for API calls
        """
        self.arxiv_client = arxiv_client

    async def fetch_and_process_papers(
        self,
        from_date: str | None = None,
        to_date: str | None = None,
        store_to_db: bool = True,
        db_session: Session | None = None,
    ) -> dict[str, Any]:
        """
        Fetch papers from arXiv and store them to database.

        The arXiv client's max_results setting (ARXIV__MAX_RESULTS) caps the papers fetched, newest first.

        Args:
            from_date: Filter papers from this date (YYYYMMDD)
            to_date: Filter papers to this date (YYYYMMDD)
            store_to_db: Whether to store results in database
            db_session: Database session (required if store_to_db=True)

        Returns:
            Dictionary with processing results and statistics
        """

        results: dict[str, Any] = {
            "papers_fetched": 0,
            "papers_stored": 0,
            "errors": [],
            "processing_time": 0,
        }

        start_time = datetime.now()

        try:
            # Step 1: Fetch paper metadata from arXiv
            papers = await self.arxiv_client.fetch_papers(
                from_date=from_date, to_date=to_date, sort_by="submittedDate", sort_order="descending"
            )

            results["papers_fetched"] = len(papers)

            if not papers:
                logger.warning("No papers found")
                return results

            # Step 2: Store to database if requested
            if store_to_db and db_session:
                logger.info("Step 2: Storing papers to database...")
                stored_count = self._store_papers_to_db(papers, db_session)
                results["papers_stored"] = stored_count
            elif store_to_db:
                logger.warning("Database storage requested but no session provided")
                results["errors"].append("Database session not provided for storage")

            # Calculate total processing time
            processing_time = (datetime.now() - start_time).total_seconds()
            results["processing_time"] = processing_time

            # Simple logging summary
            logger.info(
                f"Pipeline completed in {processing_time:.1f}s: "
                f"{results['papers_fetched']} papers, {len(results['errors'])} errors"
            )

            if results["errors"]:
                logger.warning("Errors summary:")
                for i, error in enumerate(results["errors"][:5], 1):  # Show first 5 errors
                    logger.warning(f"  {i}. {error}")
                if len(results["errors"]) > 5:
                    logger.warning(f"  ... and {len(results['errors']) - 5} more errors")

            return results

        except Exception as e:
            logger.error(f"Pipeline error: {e}")
            results["errors"].append(f"Pipeline error: {e!s}")
            raise PipelineException(f"Pipeline execution failed: {e}") from e

    def _store_papers_to_db(self, papers: list[ArxivPaper], db_session: Session) -> int:
        """
        Store paper metadata to database.

        Args:
            papers: List of ArxivPaper metadata
            db_session: Database session

        Returns:
            Number of papers stored successfully
        """
        paper_repo = PaperRepository(db_session)
        stored_count = 0

        for paper in papers:
            try:
                paper_create = PaperCreate(
                    arxiv_id=paper.arxiv_id,
                    title=paper.title,
                    authors=paper.authors,
                    abstract=paper.abstract,
                    categories=paper.categories,
                    published_date=date_parser.parse(paper.published_date),
                    pdf_url=paper.pdf_url,
                )
                stored_paper = paper_repo.upsert(paper_create)

                if stored_paper:
                    stored_count += 1
                    logger.debug(f"Stored paper {paper.arxiv_id} to database (metadata only)")

            except Exception as e:
                logger.error(f"Failed to store paper {paper.arxiv_id}: {e}")
                # A failed statement aborts the transaction, which would make every following paper fail too.
                # upsert commits each paper, so the rollback loses only this one.
                db_session.rollback()

        # Commit all changes
        try:
            db_session.commit()
            logger.info(f"Committed {stored_count} papers to database")
        except Exception as e:
            logger.error(f"Failed to commit papers to database: {e}")
            db_session.rollback()
            stored_count = 0

        return stored_count


def make_metadata_fetcher(arxiv_client: ArxivClient) -> MetadataFetcher:
    """
    Factory function to create MetadataFetcher instance.

    Args:
        arxiv_client: Configured ArxivClient

    Returns:
        MetadataFetcher instance
    """
    return MetadataFetcher(arxiv_client=arxiv_client)
