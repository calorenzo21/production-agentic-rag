"""arXiv over HTTP, faked for the arXiv client, and the responses and PDFs it sends."""

import asyncio
import io
import re
from collections import Counter
from collections.abc import AsyncIterator, Sequence

import httpx
import pypdfium2 as pdfium

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


# Made once, before any Ingestion: pdfium allows one thread at a time, and the parser calls it while arXiv sends PDFs.
ONE_PAGE_PDF = pdf()


def listed_arxiv_id(entry: str) -> str:
    """The arXiv ID, with its Version, of a Paper an entry lists."""
    match = re.search(r"<id>http://arxiv.org/abs/(.+?)</id>", entry)
    assert match is not None
    return match[1]


class FakeArxiv:
    """
    arXiv over HTTP. Its API answers a search with the feed the test makes it list, and a query by an ID without a
    Version with the Paper it lists under that ID. It keeps the queries it received. Its PDF links stream a one-page
    PDF, unless the test gives a Paper another one, and drop the connection halfway through the PDFs the test names.
    """

    def __init__(self) -> None:
        self.feed = atom_feed()
        # The entries the feed lists, by the arXiv ID of each, with its Version.
        self.entries: dict[str, str] = {}
        self.queries: list[httpx.QueryParams] = []
        self.pdfs: dict[str, bytes] = {}
        self.dropping: set[str] = set()
        # The arXiv ID of the Paper whose PDF each download asked for.
        self.downloads: list[str] = []
        # The arXiv ID of each Paper whose PDF it sent whole, in the order it finished sending them.
        self.pdfs_sent: list[str] = []
        # How many bytes of each Paper's PDF it sent, over every download.
        self.bytes_sent: Counter[str] = Counter()
        # When each request arrived, on the event loop's clock, whether it queried the API or downloaded a PDF.
        self.request_times: list[float] = []
        # The timeouts each request asked the network to enforce, in seconds, whether it queried the API or downloaded a PDF.
        self.timeouts: list[dict[str, float | None]] = []
        self.transport = httpx.MockTransport(self._respond)

    def lists(self, *entries: str) -> None:
        self.feed = atom_feed(*entries)
        self.entries = {listed_arxiv_id(entry): entry for entry in entries}

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
        if "id_list" in request.url.params:
            return httpx.Response(200, text=atom_feed(*self._found_by(request.url.params["id_list"])))
        return httpx.Response(200, text=self.feed)

    def _found_by(self, arxiv_id: str) -> list[str]:
        """The entries arXiv finds by an ID without a Version: the Paper's, which the test lists at its latest Version."""
        return [entry for listed, entry in self.entries.items() if re.sub(r"v\d+$", "", listed) == arxiv_id]

    async def _stream_pdf(self, arxiv_id: str, request: httpx.Request) -> AsyncIterator[bytes]:
        pdf_bytes = self.pdfs.get(arxiv_id, ONE_PAGE_PDF)
        if arxiv_id in self.dropping:
            yield pdf_bytes[: len(pdf_bytes) // 2]
            raise httpx.ReadError("The connection dropped", request=request)
        for start in range(0, len(pdf_bytes), CHUNK_SIZE):
            chunk = pdf_bytes[start : start + CHUNK_SIZE]
            self.bytes_sent[arxiv_id] += len(chunk)
            yield chunk
        self.pdfs_sent.append(arxiv_id)
