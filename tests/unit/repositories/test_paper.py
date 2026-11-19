from datetime import datetime

from src.db.interfaces.base import BaseDatabase
from src.repositories.paper import PaperRepository
from src.schemas.paper import PaperCreate

FIRST_METADATA = PaperCreate(
    arxiv_id="2411.00001v1",
    title="A first title",
    authors=["Ada Lovelace"],
    abstract="The abstract arXiv listed first.",
    categories=["cs.AI"],
    published_date=datetime(2024, 11, 1, 9, 30),
    pdf_url="https://arxiv.org/pdf/2411.00001v1",
)
SECOND_METADATA = PaperCreate(
    arxiv_id="2411.00001v1",
    title="A corrected title",
    authors=["Ada Lovelace", "Charles Babbage"],
    abstract="The abstract arXiv listed later.",
    categories=["cs.AI", "cs.LG"],
    published_date=datetime(2024, 11, 2, 10, 45),
    pdf_url="https://arxiv.org/pdf/2411.00001v1.pdf",
)


def test_upserting_the_same_paper_twice_holds_one_paper_with_the_second_metadata(database: BaseDatabase) -> None:
    for metadata in (FIRST_METADATA, SECOND_METADATA):
        with database.get_session() as session:
            PaperRepository(session).upsert(metadata)

    with database.get_session() as session:
        papers = PaperRepository(session).get_all()

    assert [PaperCreate.model_validate(paper, from_attributes=True) for paper in papers] == [SECOND_METADATA]
