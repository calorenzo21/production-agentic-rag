"""The disposable OpenSearch that the tests share."""

import pytest
from opensearchpy import OpenSearch


# Twice, as each run leaves an index behind, which the other must not find.
@pytest.mark.parametrize("run", ["first", "second"])
def test_a_test_finds_no_index_left_by_another(run: str, opensearch: OpenSearch) -> None:
    opensearch.indices.create(index=f"{run}-run")

    assert list(opensearch.indices.get(index="*-run")) == [f"{run}-run"]


# Twice, as each run leaves a search pipeline behind, such as the RRF pipeline, which the other must not find.
@pytest.mark.parametrize("run", ["first", "second"])
def test_a_test_finds_no_search_pipeline_left_by_another(run: str, opensearch: OpenSearch) -> None:
    opensearch.search_pipeline.put(id=f"{run}-run", body={"description": f"Left by the {run} run"})

    assert list(opensearch.search_pipeline.get()) == [f"{run}-run"]
