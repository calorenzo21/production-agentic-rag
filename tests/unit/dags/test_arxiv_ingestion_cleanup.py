"""The cleanup task of the arxiv_paper_ingestion DAG, run without Airflow: the PDFs it deletes from the PDF cache."""

import os
import time
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from src.config import get_settings

from arxiv_ingestion.cleanup import cleanup_temp_files


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
