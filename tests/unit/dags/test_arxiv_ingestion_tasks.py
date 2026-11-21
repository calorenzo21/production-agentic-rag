"""The tasks of the arxiv_paper_ingestion DAG, run without Airflow: the day a run ingests, and the PDF cache's cleanup."""

import os
import time
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from src.config import get_settings

from arxiv_ingestion.tasks import cleanup_temp_files, day_to_ingest


def test_a_scheduled_run_ingests_the_day_before_it_runs() -> None:
    # Sunday at the course's hour: a weekend run, which the course's schedule never had.
    run_day = datetime(2026, 11, 22, 6, tzinfo=UTC)

    assert day_to_ingest(logical_date=run_day, run_after=run_day) == date(2026, 11, 21)


def test_a_manual_run_without_a_logical_date_ingests_the_day_before_it_runs() -> None:
    triggered = datetime(2026, 11, 20, 15, 30, tzinfo=UTC)

    assert day_to_ingest(logical_date=None, run_after=triggered) == date(2026, 11, 19)


def test_a_manual_run_with_a_logical_date_ingests_the_day_before_that_date() -> None:
    chosen = datetime(2026, 11, 10, 6, tzinfo=UTC)
    triggered = datetime(2026, 11, 20, 15, 30, tzinfo=UTC)

    assert day_to_ingest(logical_date=chosen, run_after=triggered) == date(2026, 11, 9)


@pytest.fixture
def pdf_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """The folder the arXiv client downloads PDFs into, as its setting names it."""
    pdf_cache = tmp_path / "arxiv_pdfs"
    pdf_cache.mkdir()
    monkeypatch.setenv("ARXIV__PDF_CACHE_DIR", str(pdf_cache))
    get_settings.cache_clear()
    yield pdf_cache
    get_settings.cache_clear()


def pdf_downloaded(folder: Path, arxiv_id: str, age: timedelta) -> Path:
    """A Paper's PDF, downloaded into the folder age ago."""
    pdf = folder / f"{arxiv_id}.pdf"
    pdf.write_bytes(b"%PDF-1.7")
    then = time.time() - age.total_seconds()
    os.utime(pdf, (then, then))
    return pdf


def test_the_cleanup_deletes_only_the_pdfs_older_than_30_days_from_the_arxiv_clients_folder(
    pdf_cache: Path, tmp_path: Path
) -> None:
    pdf_downloaded(pdf_cache, "2410.00001v1", age=timedelta(days=31))
    recent = pdf_downloaded(pdf_cache, "2410.00002v1", age=timedelta(days=29))
    # Outside the arXiv client's folder, so not the cleanup's to delete, however old.
    elsewhere = pdf_downloaded(tmp_path, "2410.00003v1", age=timedelta(days=31))

    cleanup_temp_files()

    assert list(pdf_cache.iterdir()) == [recent]
    assert elsewhere.exists()
