"""Fixtures shared by the tests of the services."""

import pytest

from tests.unit.services.fake_arxiv import FakeArxiv


@pytest.fixture
def arxiv() -> FakeArxiv:
    return FakeArxiv()
