"""The OpenSearch client, against a real OpenSearch, disposable and without security, reached through the settings."""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from opensearchpy import OpenSearch
from src.exceptions import OpenSearchException
from src.services.opensearch.client import OpenSearchClient
from src.services.opensearch.factory import make_opensearch_client

# The API runs 4 workers, and each creates the chunk index at startup when it does not exist.
WORKERS = 4


def test_workers_starting_at_once_create_the_chunk_index_once_then_each_reads_its_size_without_error(
    opensearch: OpenSearch, caplog: pytest.LogCaptureFixture
) -> None:
    # Each worker is a process with its own client, which the factory builds and caches once per process.
    workers = [make_opensearch_client.__wrapped__() for _ in range(WORKERS)]
    # As close together as possible, the worst case for a race.
    start = threading.Barrier(WORKERS)

    def start_up(worker: OpenSearchClient) -> tuple[bool, dict[str, Any]]:
        start.wait()
        return worker.setup_indices()["hybrid_index"], worker.get_index_stats()

    with ThreadPoolExecutor(WORKERS) as pool:
        startups = list(pool.map(start_up, workers))

    assert sorted(created for created, _ in startups) == [False, False, False, True]
    assert [(stats.get("index_name"), stats.get("document_count")) for _, stats in startups] == [("arxiv-papers-chunks", 0)] * 4
    assert [record.getMessage() for record in caplog.records if record.levelno >= logging.ERROR] == []


def test_the_rrf_pipeline_is_found_once_the_client_has_set_it_up(opensearch: OpenSearch) -> None:
    client = make_opensearch_client.__wrapped__()
    found_before = client.rrf_pipeline_exists()

    client.setup_indices()

    assert (found_before, client.rrf_pipeline_exists()) == (False, True)


def test_looking_the_rrf_pipeline_up_fails_rather_than_finding_none_when_opensearch_does_not_answer(
    opensearch_down: None,
) -> None:
    with pytest.raises(OpenSearchException):
        make_opensearch_client.__wrapped__().rrf_pipeline_exists()
