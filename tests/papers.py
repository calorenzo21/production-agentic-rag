"""
Papers as the Ingestion stores them, and the chunk index their Chunks are indexed into, which the indexing tests and the
API tests share.
"""

from datetime import datetime
from typing import Any

from src.config import Settings
from src.db.interfaces.base import BaseDatabase
from src.repositories.paper import PaperRepository
from src.schemas.arxiv.paper import PaperCreate
from src.services.paper_indexer import make_paper_indexer


def words(count: int, *, about: str) -> str:
    """A text of so many words, each its topic followed by its position, so that no other text in a test shares them."""
    return " ".join(f"{about}{position}" for position in range(count))


def content(*sections: tuple[str, str]) -> dict[str, Any]:
    """A Paper's Content as the Ingestion stores it when Docling parses its PDF into these sections, each a title and text."""
    return {
        "raw_text": "\n\n".join(f"{title}\n\n{text}" for title, text in sections),
        "sections": [{"title": title, "content": text} for title, text in sections],
        "pdf_processed": True,
    }


def paper(arxiv_id: str, *, version: int = 1, **fields: Any) -> PaperCreate:
    """A Paper as the Ingestion stores it, held at its Version, without Content unless the test gives it."""
    metadata: dict[str, Any] = {
        "arxiv_id": arxiv_id,
        "version": version,
        "title": f"The title of {arxiv_id}",
        "authors": ["Ada Lovelace", "Charles Babbage"],
        "abstract": f"The abstract of {arxiv_id}.",
        "categories": ["cs.AI"],
        "published_date": datetime(2024, 11, 1, 9, 30),
        "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}v{version}",
    }
    return PaperCreate(**{**metadata, **fields})


def hold(database: BaseDatabase, *papers: PaperCreate) -> None:
    """Store the Papers as the Ingestion does."""
    with database.get_session() as session:
        repository = PaperRepository(session)
        for held in papers:
            repository.upsert(held)


async def hold_and_index(database: BaseDatabase, *papers: PaperCreate) -> None:
    """Store the Papers as the Ingestion does, then index them into the Index, which the API created at startup."""
    hold(database, *papers)
    await make_paper_indexer(database, Settings()).index_papers([held.arxiv_id for held in papers])


def chunk_index() -> str:
    """The chunk index: the index name followed by the chunk index's suffix, arxiv-papers-chunks by default."""
    settings = Settings().opensearch
    return f"{settings.index_name}-{settings.chunk_index_suffix}"
