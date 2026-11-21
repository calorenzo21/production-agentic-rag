"""
The Ingestion of a day, through the metadata fetcher, with arXiv faked over HTTP, Docling faked and a real PostgreSQL.
Time is virtual, so arXiv's rate limit holds without making the tests wait.
"""

import asyncio
import io
import itertools
import selectors
from collections import Counter
from collections.abc import AsyncIterator, Iterator, Sequence
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
from src.schemas.arxiv.paper import PaperBase, PaperCreate
from src.services.arxiv.client import ArxivClient
from src.services.metadata_fetcher import make_metadata_fetcher
from src.services.pdf_parser.parser import PDFParserService

DAY = "20241101"

# arXiv sends a PDF in chunks of this many bytes.
CHUNK_SIZE = 64 * 1024


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
    arXiv over HTTP. Its API answers every query with one feed and keeps the queries it received. Its PDF links stream
    a one-page PDF, unless the test gives a Paper another one, and drop the connection halfway through the PDFs the
    test names.
    """

    def __init__(self) -> None:
        self.feed = atom_feed()
        self.queries: list[httpx.QueryParams] = []
        self.pdfs: dict[str, bytes] = {}
        self.dropping: set[str] = set()
        # The arXiv ID of the Paper whose PDF each download asked for.
        self.downloads: list[str] = []
        # How many bytes of each Paper's PDF it sent, over every download.
        self.bytes_sent: Counter[str] = Counter()
        # When each request arrived, on the event loop's clock, whether it queried the API or downloaded a PDF.
        self.request_times: list[float] = []
        # The timeouts each request asked the network to enforce, in seconds, whether it queried the API or downloaded a PDF.
        self.timeouts: list[dict[str, float | None]] = []
        self.transport = httpx.MockTransport(self._respond)

    def lists(self, *entries: str) -> None:
        self.feed = atom_feed(*entries)

    def drops_the_connection_during(self, arxiv_id: str) -> None:
        self.dropping.add(arxiv_id)

    def _respond(self, request: httpx.Request) -> httpx.Response:
        self.request_times.append(asyncio.get_running_loop().time())
        self.timeouts.append(request.extensions["timeout"])
        if request.url.path.startswith("/pdf/"):
            arxiv_id = request.url.path.removeprefix("/pdf/")
            self.downloads.append(arxiv_id)
            return httpx.Response(200, content=self._stream_pdf(arxiv_id, request))
        self.queries.append(request.url.params)
        return httpx.Response(200, text=self.feed)

    async def _stream_pdf(self, arxiv_id: str, request: httpx.Request) -> AsyncIterator[bytes]:
        pdf_bytes = self.pdfs.get(arxiv_id, pdf())
        if arxiv_id in self.dropping:
            yield pdf_bytes[: len(pdf_bytes) // 2]
            raise httpx.ReadError("The connection dropped", request=request)
        for start in range(0, len(pdf_bytes), CHUNK_SIZE):
            chunk = pdf_bytes[start : start + CHUNK_SIZE]
            self.bytes_sent[arxiv_id] += len(chunk)
            yield chunk


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


class VirtualTimeLoop(asyncio.SelectorEventLoop):
    """
    An event loop whose clock stands still while a task can run, and jumps to the next timer once every task waits.
    Sleeps take no real time, yet concurrent sleeps end in the order, and at the times, they would.
    """

    def __init__(self) -> None:
        self.now = 0.0
        super().__init__(ClockJumpingSelector(self))

    def time(self) -> float:
        return self.now


class ClockJumpingSelector(selectors.DefaultSelector):
    """Instead of waiting for its loop's next timer, moves the loop's clock forward to it."""

    def __init__(self, loop: VirtualTimeLoop) -> None:
        super().__init__()
        self.loop = loop

    def select(self, timeout: float | None = None) -> list[tuple[selectors.SelectorKey, int]]:
        if timeout:
            self.loop.now += timeout
            timeout = 0
        return super().select(timeout)


class VirtualTimePolicy(asyncio.DefaultEventLoopPolicy):
    def new_event_loop(self) -> asyncio.AbstractEventLoop:
        return VirtualTimeLoop()


@pytest.fixture(scope="module")
def event_loop_policy() -> asyncio.AbstractEventLoopPolicy:
    """pytest-asyncio runs each test of this module on a VirtualTimeLoop."""
    return VirtualTimePolicy()


@pytest.fixture
def arxiv() -> FakeArxiv:
    return FakeArxiv()


@pytest.fixture
def docling() -> FakeDocling:
    return FakeDocling()


@pytest.fixture(autouse=True)
def pdf_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Each test downloads its PDFs into a cache of its own."""
    monkeypatch.setenv("ARXIV__PDF_CACHE_DIR", str(tmp_path))
    return tmp_path


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
    arxiv_client = ArxivClient(settings.arxiv, max_pdf_size_mb=settings.pdf_parser.max_file_size_mb, transport=arxiv.transport)
    fetcher = make_metadata_fetcher(arxiv_client, pdf_parser)
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


async def test_a_pdf_over_the_page_limit_stores_its_paper_without_content(
    arxiv: FakeArxiv, docling: FakeDocling, database: BaseDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PDF_PARSER__MAX_PAGES", "1")
    arxiv.lists(atom_entry("2411.00001v1"))
    arxiv.pdfs["2411.00001v1"] = pdf(pages=2)

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


async def test_requests_to_arxiv_pdf_downloads_included_come_at_least_the_rate_limit_delay_apart(
    arxiv: FakeArxiv, docling: FakeDocling, database: BaseDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARXIV__RATE_LIMIT_DELAY", "3")
    # Five Papers, whose PDFs the fetcher downloads concurrently.
    arxiv.lists(*(atom_entry(f"2411.0000{number}v1") for number in range(5, 0, -1)))

    await ingest(DAY, arxiv, docling, database)

    gaps = [later - earlier for earlier, later in itertools.pairwise(arxiv.request_times)]
    assert len(gaps) == 5
    assert min(gaps) >= 3


async def test_a_failed_download_leaves_no_partial_pdf_and_the_next_ingestion_downloads_it_again(
    arxiv: FakeArxiv, docling: FakeDocling, database: BaseDatabase, pdf_cache: Path
) -> None:
    arxiv.lists(atom_entry("2411.00001v1"))
    arxiv.drops_the_connection_during("2411.00001v1")

    await ingest(DAY, arxiv, docling, database)

    assert list(pdf_cache.iterdir()) == []
    arxiv_reachable_again = FakeArxiv()
    arxiv_reachable_again.lists(atom_entry("2411.00001v1"))
    await ingest(DAY, arxiv_reachable_again, docling, database)
    assert arxiv_reachable_again.downloads == ["2411.00001v1"]


async def test_a_pdf_over_the_size_limit_stops_downloading_leaves_no_file_and_stores_its_paper_without_content(
    arxiv: FakeArxiv, docling: FakeDocling, database: BaseDatabase, pdf_cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PDF_PARSER__MAX_FILE_SIZE_MB", "1")
    arxiv.lists(atom_entry("2411.00001v1"))
    # Padded after its end, where no PDF reader looks.
    pdf_over_the_limit = pdf() + b"%" + b"0" * 3 * 1024 * 1024
    arxiv.pdfs["2411.00001v1"] = pdf_over_the_limit

    results = await ingest(DAY, arxiv, docling, database)

    # One download, stopped before the whole PDF arrived, and not retried.
    assert arxiv.downloads == ["2411.00001v1"]
    assert arxiv.bytes_sent["2411.00001v1"] < len(pdf_over_the_limit)
    assert list(pdf_cache.iterdir()) == []
    [paper] = held_papers(database)
    assert (content(paper), paper.pdf_processed) == ((None, None, None), False)
    assert (results["pdfs_downloaded"], results["pdfs_parsed"]) == (0, 0)


async def test_requests_to_arxiv_pdf_downloads_included_time_out_after_arxiv_timeout_seconds(
    arxiv: FakeArxiv, docling: FakeDocling, database: BaseDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARXIV__TIMEOUT_SECONDS", "7")
    arxiv.lists(atom_entry("2411.00001v1"))

    await ingest(DAY, arxiv, docling, database)

    query_timeouts, download_timeouts = arxiv.timeouts
    assert query_timeouts == download_timeouts == {"connect": 7, "read": 7, "write": 7, "pool": 7}
