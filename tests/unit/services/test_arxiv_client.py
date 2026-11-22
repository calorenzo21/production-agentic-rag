"""
Fetching a Paper by its arXiv ID, through the arXiv client, with arXiv faked over HTTP.
Time is virtual, so arXiv's rate limit holds without making the tests wait.
"""

import asyncio
import itertools

import pytest
from src.config import Settings
from src.services.arxiv.client import ArxivClient

from tests.unit.services.fake_arxiv import FakeArxiv, atom_entry
from tests.unit.services.virtual_time import VirtualTimePolicy


@pytest.fixture(scope="module")
def event_loop_policy() -> asyncio.AbstractEventLoopPolicy:
    """pytest-asyncio runs each test of this module on a VirtualTimeLoop."""
    return VirtualTimePolicy()


def arxiv_client(arxiv: FakeArxiv) -> ArxivClient:
    """An arXiv client that reaches the fake arXiv, with the settings the environment holds now."""
    settings = Settings()
    return ArxivClient(settings.arxiv, max_pdf_size_mb=settings.pdf_parser.max_file_size_mb, transport=arxiv.transport)


# arXiv lists each Paper at its latest Version. Old-style IDs name an archive, which can contain a "v".
@pytest.mark.parametrize(
    ("requested", "listed"),
    [
        ("2411.00001", "2411.00001v2"),
        ("2411.00001v1", "2411.00001v2"),
        ("solv-int/9901001", "solv-int/9901001v2"),
        ("solv-int/9901001v1", "solv-int/9901001v2"),
    ],
)
async def test_fetching_a_paper_by_its_arxiv_id_with_any_version_or_none_returns_it_at_its_latest_version(
    requested: str, listed: str, arxiv: FakeArxiv
) -> None:
    arxiv.lists(atom_entry(listed, title="The Paper asked for"), atom_entry("2411.99999v1", title="Another Paper"))

    paper = await arxiv_client(arxiv).fetch_paper_by_id(requested)

    assert paper is not None
    assert (paper.arxiv_id, paper.title) == (listed, "The Paper asked for")


async def test_fetching_a_paper_by_its_arxiv_id_times_out_after_arxiv_timeout_seconds(
    arxiv: FakeArxiv, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARXIV__TIMEOUT_SECONDS", "7")
    arxiv.lists(atom_entry("2411.00001v1"))

    await arxiv_client(arxiv).fetch_paper_by_id("2411.00001")

    assert arxiv.timeouts == [{"connect": 7, "read": 7, "write": 7, "pool": 7}]


async def test_fetching_a_paper_by_its_arxiv_id_starts_at_least_the_rate_limit_delay_after_the_previous_request(
    arxiv: FakeArxiv, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARXIV__RATE_LIMIT_DELAY", "3")
    arxiv.lists(atom_entry("2411.00001v1"), atom_entry("2411.00002v1"))
    client = arxiv_client(arxiv)

    # A search, then a fetch by ID after it, then another.
    await client.fetch_papers(from_date="20241101", to_date="20241101")
    await client.fetch_paper_by_id("2411.00001")
    await client.fetch_paper_by_id("2411.00002")

    gaps = [later - earlier for earlier, later in itertools.pairwise(arxiv.request_times)]
    assert len(gaps) == 2
    assert min(gaps) >= 3
