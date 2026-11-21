"""The tasks of the arxiv_paper_ingestion DAG, run without Airflow: the day a run ingests, and the PDF cache's cleanup."""

import os
import time
from calendar import FRIDAY
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from src.config import get_settings

from arxiv_ingestion.tasks import cleanup_temp_files, day_to_ingest

EASTERN = ZoneInfo("America/New_York")


def week_of_scheduled_runs(monday: date) -> list[datetime]:
    """The scheduled runs from a Monday to the Sunday after it, each at the course's hour."""
    return [datetime(monday.year, monday.month, monday.day, 6, tzinfo=UTC) + timedelta(days=n) for n in range(7)]


def all_announced(day: date) -> datetime:
    """
    When arXiv has announced every Paper submitted on a day, which its submittedDate counts in UTC, arXiv's holidays aside.

    arXiv closes its submissions at 14:00 ET from Monday to Friday, and announces each batch at 20:00 ET that day,
    except Friday's, on Sunday.
    """
    # The day's last submission, at 23:59 UTC, comes after 14:00 ET that day, so it waits for the next cutoff.
    cutoff = datetime(day.year, day.month, day.day, 14, tzinfo=EASTERN) + timedelta(days=1)
    while cutoff.weekday() > FRIDAY:
        cutoff += timedelta(days=1)
    announcement = cutoff.replace(hour=20)
    if cutoff.weekday() == FRIDAY:
        announcement += timedelta(days=2)
    return announcement


def test_a_week_of_scheduled_runs_ingests_seven_days_in_a_row_friday_to_sunday_included() -> None:
    runs = week_of_scheduled_runs(date(2026, 11, 16))

    days = [day_to_ingest(logical_date=run, run_after=run) for run in runs]

    # From Thursday to Wednesday, so the weeks on either side leave no day out.
    assert days == [date(2026, 11, d) for d in range(12, 19)]


# The runs keep to UTC while arXiv keeps to ET, so a week on each side of daylight saving time.
@pytest.mark.parametrize("monday", [date(2026, 11, 16), date(2026, 7, 13)], ids=["EST", "EDT"])
def test_every_scheduled_run_ingests_a_day_arxiv_has_announced_all_the_papers_of(monday: date) -> None:
    for run in week_of_scheduled_runs(monday):
        day = day_to_ingest(logical_date=run, run_after=run)

        assert all_announced(day) <= run, f"The run of {run:%A %d} ingests {day:%A %d} before arXiv has announced it all"


def test_a_manual_run_without_a_logical_date_ingests_the_day_four_days_before_it_was_triggered() -> None:
    triggered = datetime(2026, 11, 20, 15, 30, tzinfo=UTC)

    assert day_to_ingest(logical_date=None, run_after=triggered) == date(2026, 11, 16)


def test_a_manual_run_with_a_logical_date_ingests_the_day_four_days_before_that_date() -> None:
    chosen = datetime(2026, 11, 10, 6, tzinfo=UTC)
    triggered = datetime(2026, 11, 20, 15, 30, tzinfo=UTC)

    assert day_to_ingest(logical_date=chosen, run_after=triggered) == date(2026, 11, 6)


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
