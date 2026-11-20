from src.config import get_settings

from .client import ArxivClient


def make_arxiv_client() -> ArxivClient:
    """
    Factory function to create an arXiv client instance.

    Returns:
        ArxivClient: An instance of the arXiv client.
    """
    # Get settings from centralized config
    settings = get_settings()

    # Create arXiv client with explicit settings. A PDF download stops at the size the PDF parser accepts.
    client = ArxivClient(settings=settings.arxiv, max_pdf_size_mb=settings.pdf_parser.max_file_size_mb)

    return client
