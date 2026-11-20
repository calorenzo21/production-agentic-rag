"""The Ingestion of a day, through the metadata fetcher, with arXiv faked over HTTP and a real PostgreSQL."""

from collections.abc import Iterator, Sequence
from datetime import datetime

import httpx
import pytest
from sqlalchemy import text
from src.config import Settings
from src.db.interfaces.base import BaseDatabase
from src.exceptions import PipelineException
from src.repositories.paper import PaperRepository
from src.schemas.paper import PaperCreate
from src.services.arxiv.client import ArxivClient
from src.services.metadata_fetcher import make_metadata_fetcher

DAY = "20241101"


def atom_entry(
    arxiv_id: str,
    *,
    title: str = "A title",
    authors: Sequence[str] = ("Ada Lovelace",),
    abstract: str = "An abstract.",
    categories: Sequence[str] = ("cs.AI",),
    published: str = "2024-11-01T09:30:00Z",
) -> str:
    """One Paper as arXiv's API lists it."""
    return f"""
    <entry>
      <id>http://arxiv.org/abs/{arxiv_id}</id>
      <updated>{published}</updated>
      <published>{published}</published>
      <title>{title}</title>
      <summary>{abstract}</summary>
      {"".join(f"<author><name>{author}</name></author>" for author in authors)}
      <link href="http://arxiv.org/abs/{arxiv_id}" rel="alternate" type="text/html"/>
      <link title="pdf" href="http://arxiv.org/pdf/{arxiv_id}" rel="related" type="application/pdf"/>
      <arxiv:primary_category term="{categories[0]}" scheme="http://arxiv.org/schemas/atom"/>
      {"".join(f'<category term="{category}" scheme="http://arxiv.org/schemas/atom"/>' for category in categories)}
    </entry>"""


def atom_feed(*entries: str, doctype: str = "") -> str:
    """A response of arXiv's API, listing the entries in the order given."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>{doctype}
    <feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/"
          xmlns:arxiv="http://arxiv.org/schemas/atom">
      <title>arXiv Query</title>
      <opensearch:totalResults>{len(entries)}</opensearch:totalResults>
      <opensearch:startIndex>0</opensearch:startIndex>
      {"".join(entries)}
    </feed>"""


def entity_expansion(levels: int) -> str:
    """A DOCTYPE declaring an entity, &lol{levels};, that expands to 3 * 10**levels characters."""
    declarations = ['<!ENTITY lol0 "lol">'] + [
        f'<!ENTITY lol{level} "{f"&lol{level - 1};" * 10}">' for level in range(1, levels + 1)
    ]
    return f"<!DOCTYPE feed [{''.join(declarations)}]>"


class FakeArxiv:
    """arXiv's API over HTTP, answering every query with one feed and keeping the queries it received."""

    def __init__(self) -> None:
        self.feed = atom_feed()
        self.queries: list[httpx.QueryParams] = []
        self.transport = httpx.MockTransport(self._respond)

    def lists(self, *entries: str) -> None:
        self.feed = atom_feed(*entries)

    def _respond(self, request: httpx.Request) -> httpx.Response:
        self.queries.append(request.url.params)
        return httpx.Response(200, text=self.feed)


@pytest.fixture
def arxiv() -> FakeArxiv:
    return FakeArxiv()


@pytest.fixture
def paper_postgresql_refuses(database: BaseDatabase) -> Iterator[str]:
    """The arXiv ID of a Paper that PostgreSQL refuses to store."""
    with database.get_session() as session:
        session.execute(text("ALTER TABLE papers ADD CONSTRAINT refuses_one_paper CHECK (arxiv_id <> '2411.00002v1')"))
        session.commit()
    yield "2411.00002v1"
    with database.get_session() as session:
        session.execute(text("ALTER TABLE papers DROP CONSTRAINT refuses_one_paper"))
        session.commit()


async def ingest(day: str, arxiv: FakeArxiv, database: BaseDatabase) -> None:
    """Ingest the Papers submitted on the day, with the settings the environment holds now."""
    fetcher = make_metadata_fetcher(ArxivClient(Settings().arxiv, transport=arxiv.transport))
    with database.get_session() as session:
        await fetcher.fetch_and_process_papers(from_date=day, to_date=day, db_session=session)


def held_papers(database: BaseDatabase) -> list[PaperCreate]:
    with database.get_session() as session:
        return [PaperCreate.model_validate(paper, from_attributes=True) for paper in PaperRepository(session).get_all()]


async def test_ingesting_a_day_stores_the_metadata_of_its_papers(arxiv: FakeArxiv, database: BaseDatabase) -> None:
    arxiv.lists(
        atom_entry(
            "2411.00002v2",
            title="A later Paper",
            authors=["Grace Hopper", "Alan Turing"],
            abstract="The abstract of the later Paper.",
            categories=["cs.AI", "cs.LG"],
            published="2024-11-01T15:00:00Z",
        ),
        atom_entry(
            "2411.00001v1",
            title="An earlier Paper",
            authors=["Ada Lovelace"],
            abstract="The abstract of the earlier Paper.",
            categories=["cs.AI"],
            published="2024-11-01T09:30:00Z",
        ),
    )

    await ingest(DAY, arxiv, database)

    assert held_papers(database) == [
        PaperCreate(
            arxiv_id="2411.00002v2",
            title="A later Paper",
            authors=["Grace Hopper", "Alan Turing"],
            abstract="The abstract of the later Paper.",
            categories=["cs.AI", "cs.LG"],
            published_date=datetime(2024, 11, 1, 15, 0),
            pdf_url="https://arxiv.org/pdf/2411.00002v2",
        ),
        PaperCreate(
            arxiv_id="2411.00001v1",
            title="An earlier Paper",
            authors=["Ada Lovelace"],
            abstract="The abstract of the earlier Paper.",
            categories=["cs.AI"],
            published_date=datetime(2024, 11, 1, 9, 30),
            pdf_url="https://arxiv.org/pdf/2411.00001v1",
        ),
    ]


async def test_ingesting_a_day_again_holds_the_same_papers_without_duplicates(arxiv: FakeArxiv, database: BaseDatabase) -> None:
    arxiv.lists(atom_entry("2411.00002v1", published="2024-11-01T15:00:00Z"), atom_entry("2411.00001v1"))
    await ingest(DAY, arxiv, database)
    held_after_the_first_ingestion = held_papers(database)

    await ingest(DAY, arxiv, database)

    assert held_papers(database) == held_after_the_first_ingestion


async def test_the_query_asks_arxiv_for_arxiv_max_results_papers_newest_first(
    arxiv: FakeArxiv, database: BaseDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARXIV__MAX_RESULTS", "3")

    await ingest(DAY, arxiv, database)

    [query] = arxiv.queries
    assert (query["max_results"], query["sortBy"], query["sortOrder"]) == ("3", "submittedDate", "descending")


async def test_variables_without_the_arxiv_prefix_leave_the_query_unchanged(
    arxiv: FakeArxiv, database: BaseDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    await ingest(DAY, arxiv, database)
    monkeypatch.setenv("MAX_RESULTS", "7")
    monkeypatch.setenv("SEARCH_CATEGORY", "math.CO")

    await ingest(DAY, arxiv, database)

    query_before, query_after = arxiv.queries
    assert query_after == query_before


# The Python of this repository refuses the billion laughs itself, but not the smaller expansion below its limit.
@pytest.mark.parametrize("levels", [9, 5], ids=["billion laughs", "below the limit of Python's own parser"])
async def test_a_response_that_expands_entities_is_refused(levels: int, arxiv: FakeArxiv, database: BaseDatabase) -> None:
    arxiv.feed = atom_feed(atom_entry("2411.00001v1", title=f"&lol{levels};"), doctype=entity_expansion(levels))

    with pytest.raises(PipelineException):
        await ingest(DAY, arxiv, database)

    assert held_papers(database) == []


async def test_a_paper_that_fails_to_store_leaves_the_days_other_papers_stored(
    arxiv: FakeArxiv, database: BaseDatabase, paper_postgresql_refuses: str
) -> None:
    arxiv.lists(
        atom_entry("2411.00003v1", published="2024-11-01T18:00:00Z"),
        atom_entry(paper_postgresql_refuses, published="2024-11-01T15:00:00Z"),
        atom_entry("2411.00001v1", published="2024-11-01T09:30:00Z"),
    )

    await ingest(DAY, arxiv, database)

    assert [paper.arxiv_id for paper in held_papers(database)] == ["2411.00003v1", "2411.00001v1"]
