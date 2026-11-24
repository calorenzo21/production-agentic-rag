"""The disposable OpenSearch that the tests share."""

import pytest
from opensearchpy import OpenSearch


# Twice, as each run leaves an index behind, which the other must not find.
@pytest.mark.parametrize("run", ["first", "second"])
def test_a_test_finds_no_index_left_by_another(run: str, opensearch: OpenSearch) -> None:
    opensearch.indices.create(index=f"{run}-run")

    assert list(opensearch.indices.get(index="*-run")) == [f"{run}-run"]
