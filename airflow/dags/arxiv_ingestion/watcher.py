"""Airflow's watcher, which the course's DAG does not have (README.md)."""


def fail_the_run() -> None:
    """Fail the run. The DAG's watcher calls it once another task has failed."""
    raise RuntimeError("A task of this run failed, so the run fails too")
