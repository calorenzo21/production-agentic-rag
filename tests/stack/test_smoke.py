import os
import time

import httpx
import pytest

from tests.stack.support import REPOSITORY, Stack, psql, psql_over_tcp, run_compose, services

ONE_SHOT_SERVICES = {"migrate", "airflow-init"}
EXPECTED_SERVICES = {
    "postgres",
    "opensearch",
    "opensearch-dashboards",
    "api",
    "airflow-apiserver",
    "airflow-scheduler",
    "airflow-dag-processor",
    "airflow-triggerer",
} | ONE_SHOT_SERVICES


def test_every_service_reports_healthy() -> None:
    state = {service["Service"]: service for service in services()}

    assert EXPECTED_SERVICES <= state.keys()
    for name, service in state.items():
        if name in ONE_SHOT_SERVICES:
            assert service["State"] == "exited" and service["ExitCode"] == 0, name
        else:
            assert service["Health"] == "healthy", name


def test_liveness_responds(stack: Stack) -> None:
    response = httpx.get(f"{stack.api_url}/api/v1/health/live", verify=stack.verify)

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_really_reaches_every_dependency(stack: Stack) -> None:
    response = httpx.get(f"{stack.api_url}/api/v1/health", verify=stack.verify, timeout=10)

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "dependencies": {"postgres": "ok", "opensearch": "ok", "ollama": "ok"},
    }


def test_migrations_ran_before_the_api_started() -> None:
    version = psql("paper_curator", "SELECT version_num FROM alembic_version")

    assert version.returncode == 0, version.stderr
    assert version.stdout.strip() == "0001"


def test_each_database_user_reaches_its_own_database(stack: Stack) -> None:
    curator = psql_over_tcp("curator", stack.curator_db_password, "paper_curator", "SELECT 1")
    airflow = psql_over_tcp("airflow", stack.airflow_db_password, "airflow", "SELECT 1")

    assert curator.stdout.strip() == "1", curator.stderr
    assert airflow.stdout.strip() == "1", airflow.stderr


def test_airflow_cannot_reach_paper_data(stack: Stack) -> None:
    attempt = psql_over_tcp("airflow", stack.airflow_db_password, "paper_curator", "SELECT 1")

    assert attempt.returncode != 0
    assert "permission denied for database" in attempt.stderr


def test_the_application_cannot_reach_airflow_data(stack: Stack) -> None:
    attempt = psql_over_tcp("curator", stack.curator_db_password, "airflow", "SELECT 1")

    assert attempt.returncode != 0
    assert "permission denied for database" in attempt.stderr


def test_airflow_runs_the_foundation_dag(stack: Stack) -> None:
    with httpx.Client(base_url=stack.airflow_url, verify=stack.verify, timeout=30) as airflow:
        token = airflow.post(
            "/auth/token", json={"username": "admin", "password": stack.airflow_password}
        )
        assert token.status_code in (200, 201), token.text
        airflow.headers["Authorization"] = f"Bearer {token.json()['access_token']}"

        deadline = time.monotonic() + 120
        while airflow.get("/api/v2/dags/foundation_check").status_code != 200:
            assert time.monotonic() < deadline, "the DAG was never parsed"
            time.sleep(2)

        unpaused = airflow.patch("/api/v2/dags/foundation_check", json={"is_paused": False})
        assert unpaused.status_code == 200, unpaused.text
        run = airflow.post("/api/v2/dags/foundation_check/dagRuns", json={"logical_date": None})
        assert run.status_code == 200, run.text
        run_id = run.json()["dag_run_id"]

        deadline = time.monotonic() + 180
        while True:
            state = airflow.get(f"/api/v2/dags/foundation_check/dagRuns/{run_id}").json()["state"]
            if state in ("success", "failed"):
                break
            assert time.monotonic() < deadline, f"the run is stuck in {state}"
            time.sleep(2)

    assert state == "success"


def test_hot_reload_picks_up_a_code_change(stack: Stack) -> None:
    if stack.mode != "dev":
        pytest.skip("hot reload exists only in development mode")
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    os.utime(REPOSITORY / "src/paper_curator/main.py")

    deadline = time.monotonic() + 30
    while "Reloading" not in run_compose("logs", "--since", since, "api").stdout:
        assert time.monotonic() < deadline, "the API did not reload"
        time.sleep(1)


def test_development_ports_are_bound_to_loopback(stack: Stack) -> None:
    if stack.mode != "dev":
        pytest.skip("development mode only")

    published = [
        publisher
        for service in services()
        for publisher in service["Publishers"]
        if publisher["PublishedPort"]
    ]

    assert published
    assert {publisher["URL"] for publisher in published} == {"127.0.0.1"}
