"""
The PDF cleanup. The course's is a Bash command in the DAG, which deletes PDFs from /tmp, where the arXiv client never
downloads them (README.md).
"""

import logging
import time
from datetime import timedelta

from src.services.arxiv.factory import make_arxiv_client

logger = logging.getLogger(__name__)

# How long the cleanup keeps a downloaded PDF.
PDF_RETENTION = timedelta(days=30)


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
