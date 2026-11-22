"""The API's Paper endpoints, against a real PostgreSQL that holds a Paper with its Content."""

from datetime import datetime

import pytest
from httpx import AsyncClient
from src.db.interfaces.base import BaseDatabase
from src.repositories.paper import PaperRepository
from src.schemas.arxiv.paper import PaperCreate

# A Paper the curator holds at its second Version.
ARXIV_ID = "2411.00001"
VERSION = 2

# What a Paper holds as its Content.
CONTENT = {"raw_text", "sections", "references"}


@pytest.fixture
def paper_with_content(database: BaseDatabase) -> PaperCreate:
    """A Paper the curator holds with its Content."""
    paper = PaperCreate(
        arxiv_id=ARXIV_ID,
        version=VERSION,
        title="A title",
        authors=["Ada Lovelace"],
        abstract="An abstract.",
        categories=["cs.AI"],
        published_date=datetime(2024, 11, 1, 9, 30),
        pdf_url=f"https://arxiv.org/pdf/{ARXIV_ID}v{VERSION}",
        raw_text="A title\n\nIntroduction\n\nThe Content.",
        sections=[{"title": "Introduction", "content": "The Content."}],
        references=[],
        parser_used="docling",
        pdf_processed=True,
        pdf_processing_date=datetime(2024, 11, 5, 6, 0),
    )
    with database.get_session() as session:
        PaperRepository(session).upsert(paper)
    return paper


async def test_listing_papers_returns_their_metadata_without_their_content(
    paper_with_content: PaperCreate, client: AsyncClient
) -> None:
    response = await client.get("/api/v1/papers/")

    [paper] = response.json()["papers"]
    assert (paper["arxiv_id"], paper["version"], paper["title"], paper["pdf_processed"]) == (ARXIV_ID, VERSION, "A title", True)
    assert set(paper) & CONTENT == set()


async def test_a_papers_details_include_its_content(paper_with_content: PaperCreate, client: AsyncClient) -> None:
    response = await client.get(f"/api/v1/papers/{ARXIV_ID}")

    paper = response.json()
    assert (paper["raw_text"], paper["sections"], paper["references"]) == (
        "A title\n\nIntroduction\n\nThe Content.",
        [{"title": "Introduction", "content": "The Content."}],
        [],
    )


@pytest.mark.parametrize("requested", ["2411.00001", "2411.00001v2", "2411.00001v1"])
async def test_a_paper_is_found_by_its_arxiv_id_with_any_version_or_none(
    requested: str, paper_with_content: PaperCreate, client: AsyncClient
) -> None:
    response = await client.get(f"/api/v1/papers/{requested}")

    assert response.status_code == 200
    paper = response.json()
    assert (paper["arxiv_id"], paper["version"]) == (ARXIV_ID, VERSION)
