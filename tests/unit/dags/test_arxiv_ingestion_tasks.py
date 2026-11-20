"""The day a run of the arxiv_paper_ingestion DAG ingests, worked out without Airflow."""

from datetime import UTC, date, datetime

from arxiv_ingestion.tasks import day_to_ingest


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
