"""The Ingestion of a day, through the metadata fetcher, with arXiv faked over HTTP, Docling faked and a real PostgreSQL."""

import io
from collections.abc import Iterator, Sequence
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pypdfium2 as pdfium
import pytest
from docling_core.types.doc import DocItemLabel, DoclingDocument
from sqlalchemy import text
from src.config import Settings
from src.db.interfaces.base import BaseDatabase
from src.exceptions import PipelineException
from src.repositories.paper import PaperRepository
from src.schemas.paper import PaperBase, PaperCreate
from src.services.arxiv.client import ArxivClient
from src.services.metadata_fetcher import make_metadata_fetcher
from src.services.pdf_parser.parser import PDFParserService

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


def pdf(pages: int = 1) -> bytes:
    """A PDF of blank pages."""
    document = pdfium.PdfDocument.new()
    for _ in range(pages):
        document.new_page(612, 792)
    buffer = io.BytesIO()
    document.save(buffer)
    document.close()
    return buffer.getvalue()


class FakeArxiv:
    """
    arXiv over HTTP. Its API answers every query with one feed and keeps the queries it received. Its PDF links serve
    a one-page PDF, unless the test gives a Paper another one.
    """

    def __init__(self) -> None:
        self.feed = atom_feed()
        self.queries: list[httpx.QueryParams] = []
        self.pdfs: dict[str, bytes] = {}
        self.transport = httpx.MockTransport(self._respond)

    def lists(self, *entries: str) -> None:
        self.feed = atom_feed(*entries)

    def _respond(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/pdf/"):
            return httpx.Response(200, content=self.pdfs.get(request.url.path.removeprefix("/pdf/"), pdf()))
        self.queries.append(request.url.params)
        return httpx.Response(200, text=self.feed)


def extracted_document(arxiv_id: str) -> DoclingDocument:
    """The document Docling extracts from a Paper's PDF: a title, then one section."""
    document = DoclingDocument(name=arxiv_id)
    document.add_title(text=f"Paper {arxiv_id}")
    document.add_heading(text="Introduction")
    document.add_text(label=DocItemLabel.TEXT, text=f"The Content of {arxiv_id}.")
    return document


class FakeDocling:
    """Docling's converter, which extracts each Paper's document from its PDF, or fails on the Papers the test names."""

    def __init__(self) -> None:
        self.failing: set[str] = set()

    def fails_on(self, arxiv_id: str) -> None:
        self.failing.add(arxiv_id)

    def convert(self, source: str, max_num_pages: int, max_file_size: int) -> SimpleNamespace:
        arxiv_id = Path(source).stem
        if arxiv_id in self.failing:
            raise RuntimeError(f"Docling failed on {arxiv_id}")
        return SimpleNamespace(document=extracted_document(arxiv_id))


@pytest.fixture
def arxiv() -> FakeArxiv:
    return FakeArxiv()


@pytest.fixture
def docling() -> FakeDocling:
    return FakeDocling()


@pytest.fixture(autouse=True)
def pdf_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test downloads its PDFs into a cache of its own, without waiting between requests."""
    monkeypatch.setenv("ARXIV__PDF_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("ARXIV__RATE_LIMIT_DELAY", "0")


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


async def ingest(day: str, arxiv: FakeArxiv, docling: FakeDocling, database: BaseDatabase) -> dict[str, Any]:
    """Ingest the Papers submitted on the day, with the settings the environment holds now, and return its counts."""
    settings = Settings()
    pdf_parser = PDFParserService(
        max_pages=settings.pdf_parser.max_pages,
        max_file_size_mb=settings.pdf_parser.max_file_size_mb,
        do_ocr=settings.pdf_parser.do_ocr,
        do_table_structure=settings.pdf_parser.do_table_structure,
        converter=docling,
    )
    fetcher = make_metadata_fetcher(ArxivClient(settings.arxiv, transport=arxiv.transport), pdf_parser)
    with database.get_session() as session:
        return await fetcher.fetch_and_process_papers(from_date=day, to_date=day, db_session=session)


def held_papers(database: BaseDatabase) -> list[PaperCreate]:
    """The Papers the curator holds, newest first, with their metadata, Content and processing metadata."""
    with database.get_session() as session:
        return [PaperCreate.model_validate(paper, from_attributes=True) for paper in PaperRepository(session).get_all()]


def held_metadata(database: BaseDatabase) -> list[PaperBase]:
    return [PaperBase.model_validate(paper.model_dump()) for paper in held_papers(database)]


# What a Paper holds besides its metadata: its Content and the processing metadata.
CONTENT_AND_PROCESSING = {
    "raw_text",
    "sections",
    "references",
    "parser_used",
    "parser_metadata",
    "pdf_processed",
    "pdf_processing_date",
}


def content(paper: PaperCreate) -> tuple[str | None, list[dict[str, Any]] | None, list[dict[str, Any]] | None]:
    return paper.raw_text, paper.sections, paper.references


async def test_ingesting_a_day_stores_the_metadata_of_its_papers(
    arxiv: FakeArxiv, docling: FakeDocling, database: BaseDatabase
) -> None:
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

    await ingest(DAY, arxiv, docling, database)

    assert held_metadata(database) == [
        PaperBase(
            arxiv_id="2411.00002v2",
            title="A later Paper",
            authors=["Grace Hopper", "Alan Turing"],
            abstract="The abstract of the later Paper.",
            categories=["cs.AI", "cs.LG"],
            published_date=datetime(2024, 11, 1, 15, 0),
            pdf_url="https://arxiv.org/pdf/2411.00002v2",
        ),
        PaperBase(
            arxiv_id="2411.00001v1",
            title="An earlier Paper",
            authors=["Ada Lovelace"],
            abstract="The abstract of the earlier Paper.",
            categories=["cs.AI"],
            published_date=datetime(2024, 11, 1, 9, 30),
            pdf_url="https://arxiv.org/pdf/2411.00001v1",
        ),
    ]


async def test_ingesting_a_day_again_holds_the_same_papers_without_duplicates(
    arxiv: FakeArxiv, docling: FakeDocling, database: BaseDatabase
) -> None:
    arxiv.lists(atom_entry("2411.00002v1", published="2024-11-01T15:00:00Z"), atom_entry("2411.00001v1"))
    await ingest(DAY, arxiv, docling, database)
    held_after_the_first_ingestion = held_metadata(database)

    await ingest(DAY, arxiv, docling, database)

    assert held_metadata(database) == held_after_the_first_ingestion


async def test_the_query_asks_arxiv_for_arxiv_max_results_papers_newest_first(
    arxiv: FakeArxiv, docling: FakeDocling, database: BaseDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARXIV__MAX_RESULTS", "3")

    await ingest(DAY, arxiv, docling, database)

    [query] = arxiv.queries
    assert (query["max_results"], query["sortBy"], query["sortOrder"]) == ("3", "submittedDate", "descending")


async def test_variables_without_the_arxiv_prefix_leave_the_query_unchanged(
    arxiv: FakeArxiv, docling: FakeDocling, database: BaseDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    await ingest(DAY, arxiv, docling, database)
    monkeypatch.setenv("MAX_RESULTS", "7")
    monkeypatch.setenv("SEARCH_CATEGORY", "math.CO")

    await ingest(DAY, arxiv, docling, database)

    query_before, query_after = arxiv.queries
    assert query_after == query_before


# The Python of this repository refuses the billion laughs itself, but not the smaller expansion below its limit.
@pytest.mark.parametrize("levels", [9, 5], ids=["billion laughs", "below the limit of Python's own parser"])
async def test_a_response_that_expands_entities_is_refused(
    levels: int, arxiv: FakeArxiv, docling: FakeDocling, database: BaseDatabase
) -> None:
    arxiv.feed = atom_feed(atom_entry("2411.00001v1", title=f"&lol{levels};"), doctype=entity_expansion(levels))

    with pytest.raises(PipelineException):
        await ingest(DAY, arxiv, docling, database)

    assert held_papers(database) == []


async def test_a_paper_that_fails_to_store_leaves_the_days_other_papers_stored(
    arxiv: FakeArxiv, docling: FakeDocling, database: BaseDatabase, paper_postgresql_refuses: str
) -> None:
    arxiv.lists(
        atom_entry("2411.00003v1", published="2024-11-01T18:00:00Z"),
        atom_entry(paper_postgresql_refuses, published="2024-11-01T15:00:00Z"),
        atom_entry("2411.00001v1", published="2024-11-01T09:30:00Z"),
    )

    await ingest(DAY, arxiv, docling, database)

    assert [paper.arxiv_id for paper in held_papers(database)] == ["2411.00003v1", "2411.00001v1"]


async def test_a_parsed_paper_is_stored_with_its_content_and_marked_as_processed(
    arxiv: FakeArxiv, docling: FakeDocling, database: BaseDatabase
) -> None:
    arxiv.lists(atom_entry("2411.00001v1"))

    await ingest(DAY, arxiv, docling, database)

    [paper] = held_papers(database)
    assert content(paper) == (
        "Paper 2411.00001v1\n\nIntroduction\n\nThe Content of 2411.00001v1.",
        [{"title": "Introduction", "content": "The Content of 2411.00001v1."}],
        [],
    )
    assert (paper.parser_used, paper.pdf_processed) == ("docling", True)
    assert paper.pdf_processing_date is not None


async def test_a_paper_whose_pdf_fails_to_parse_is_stored_without_content(
    arxiv: FakeArxiv, docling: FakeDocling, database: BaseDatabase
) -> None:
    arxiv.lists(atom_entry("2411.00002v1", published="2024-11-01T15:00:00Z"), atom_entry("2411.00001v1"))
    docling.fails_on("2411.00002v1")

    results = await ingest(DAY, arxiv, docling, database)

    failed, parsed = held_papers(database)
    assert (failed.arxiv_id, content(failed), failed.pdf_processed) == ("2411.00002v1", (None, None, None), False)
    assert (parsed.arxiv_id, parsed.pdf_processed) == ("2411.00001v1", True)
    assert (results["pdfs_downloaded"], results["pdfs_parsed"]) == (2, 1)


@pytest.mark.parametrize(
    ("limit", "pdf_over_the_limit"),
    [
        (("PDF_PARSER__MAX_PAGES", "1"), pdf(pages=2)),
        # Padded after its end, where no PDF reader looks.
        (("PDF_PARSER__MAX_FILE_SIZE_MB", "1"), pdf(pages=1) + b"%" + b"0" * 1024 * 1024),
    ],
    ids=["over the page limit", "over the size limit"],
)
async def test_a_pdf_over_the_page_or_size_limit_stores_its_paper_without_content(
    limit: tuple[str, str],
    pdf_over_the_limit: bytes,
    arxiv: FakeArxiv,
    docling: FakeDocling,
    database: BaseDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(*limit)
    arxiv.lists(atom_entry("2411.00001v1"))
    arxiv.pdfs["2411.00001v1"] = pdf_over_the_limit

    results = await ingest(DAY, arxiv, docling, database)

    [paper] = held_papers(database)
    assert (content(paper), paper.pdf_processed) == ((None, None, None), False)
    assert (results["pdfs_downloaded"], results["pdfs_parsed"]) == (1, 0)


async def test_variables_without_the_pdf_parser_prefix_leave_the_limits_unchanged(
    arxiv: FakeArxiv, docling: FakeDocling, database: BaseDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAX_PAGES", "1")
    arxiv.lists(atom_entry("2411.00001v1"))
    arxiv.pdfs["2411.00001v1"] = pdf(pages=2)

    await ingest(DAY, arxiv, docling, database)

    [paper] = held_papers(database)
    assert paper.pdf_processed


async def test_a_failed_parse_keeps_the_content_a_paper_already_has_and_updates_its_metadata(
    arxiv: FakeArxiv, docling: FakeDocling, database: BaseDatabase
) -> None:
    arxiv.lists(atom_entry("2411.00001v1", title="A first title"))
    await ingest(DAY, arxiv, docling, database)
    [held_with_content] = held_papers(database)
    assert held_with_content.pdf_processed
    arxiv.lists(atom_entry("2411.00001v1", title="A corrected title"))
    docling.fails_on("2411.00001v1")

    await ingest(DAY, arxiv, docling, database)

    [paper] = held_papers(database)
    assert paper.title == "A corrected title"
    assert paper.model_dump(include=CONTENT_AND_PROCESSING) == held_with_content.model_dump(include=CONTENT_AND_PROCESSING)


async def test_ingesting_a_day_again_stores_the_content_of_a_paper_whose_pdf_failed_to_parse(
    arxiv: FakeArxiv, docling: FakeDocling, database: BaseDatabase
) -> None:
    arxiv.lists(atom_entry("2411.00001v1"))
    docling.fails_on("2411.00001v1")
    await ingest(DAY, arxiv, docling, database)

    await ingest(DAY, arxiv, FakeDocling(), database)

    [paper] = held_papers(database)
    assert content(paper) == (
        "Paper 2411.00001v1\n\nIntroduction\n\nThe Content of 2411.00001v1.",
        [{"title": "Introduction", "content": "The Content of 2411.00001v1."}],
        [],
    )
    assert paper.pdf_processed
