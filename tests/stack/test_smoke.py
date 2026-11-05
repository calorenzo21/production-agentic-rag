"""Checks a running stack: `make start`, then `make smoke`.

These tests guard what the course does not give us: its services working together, and every
security Deviation. README.md lists them.
"""

import json
import re
import ssl
import subprocess
from pathlib import Path

import httpx
import psycopg2
import pytest
from dotenv import dotenv_values

REPOSITORY = Path(__file__).parents[2]
ENV = dotenv_values(REPOSITORY / ".env")
OPENSEARCH_URL = "https://localhost:9200"
CERTIFICATE_AUTHORITY = ssl.create_default_context(cafile=REPOSITORY / "secrets/opensearch/ca.crt")
APP_LOGIN = ("rag_app", ENV["OPENSEARCH_APP_PASSWORD"])


def compose(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", "compose", *arguments], cwd=REPOSITORY, check=check, capture_output=True, text=True)


def services() -> dict[str, dict]:
    output = compose("ps", "--all", "--format", "json").stdout
    return {service["Service"]: service for service in map(json.loads, output.splitlines())}


def superuser_sql(database: str, sql: str) -> str:
    """Run SQL as the superuser, over the socket inside the container."""
    return compose("exec", "-T", "postgres", "psql", "-U", "postgres", "-d", database, "-tAc", sql).stdout.strip()


def connect(user: str, password: str, database: str) -> None:
    """Log in from outside the container: PostgreSQL trusts loopback connections from inside it."""
    psycopg2.connect(host="localhost", port=5432, user=user, password=password, dbname=database).close()


def test_every_service_is_healthy() -> None:
    state = services()

    assert state.keys() == {"migrate", "api", "opensearch", "opensearch-dashboards", "airflow", "postgres"}
    assert state.pop("migrate")["ExitCode"] == 0
    for name, service in state.items():
        assert service["Health"] == "healthy", name


def test_ports_are_published_on_loopback_only() -> None:
    published = [port for service in services().values() for port in service["Publishers"] if port["PublishedPort"]]

    assert published
    assert {port["URL"] for port in published} == {"127.0.0.1"}


def test_the_api_reaches_the_database_and_native_ollama() -> None:
    response = httpx.get("http://localhost:8000/api/v1/health", timeout=10)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok", body
    assert body["services"]["database"]["status"] == "healthy"
    assert body["services"]["ollama"]["status"] == "healthy"


def test_the_api_does_not_run_as_root() -> None:
    assert compose("exec", "-T", "api", "id", "-u").stdout.strip() != "0"


def test_migrations_create_the_schema_the_models_expect() -> None:
    assert superuser_sql("rag_db", "SELECT to_regclass('public.papers')") == "papers"
    drift = compose("exec", "-T", "api", "alembic", "check", check=False)
    assert drift.returncode == 0, drift.stdout + drift.stderr


def test_the_course_database_password_is_rejected() -> None:
    with pytest.raises(psycopg2.OperationalError, match="password authentication failed"):
        connect("rag_user", "rag_password", "rag_db")
    connect("rag_user", ENV["RAG_DB_PASSWORD"], "rag_db")


def test_the_application_user_is_not_a_superuser() -> None:
    assert superuser_sql("rag_db", "SELECT rolsuper FROM pg_roles WHERE rolname = 'rag_user'") == "f"


def test_airflow_metadata_lives_in_its_own_database() -> None:
    assert superuser_sql("airflow", "SELECT to_regclass('public.dag')") == "dag"
    assert superuser_sql("rag_db", "SELECT to_regclass('public.dag')") == ""


@pytest.mark.parametrize(
    ("user", "password", "database"),
    [("airflow", ENV["AIRFLOW_DB_PASSWORD"], "rag_db"), ("rag_user", ENV["RAG_DB_PASSWORD"], "airflow")],
)
def test_database_users_cannot_cross_into_each_others_data(user: str, password: str, database: str) -> None:
    with pytest.raises(psycopg2.OperationalError, match="permission denied for database"):
        connect(user, password, database)


def test_airflow_tasks_can_use_the_application_database_code() -> None:
    code = "from src.db.factory import make_database; from src.models import Paper; make_database()"

    attempt = compose("exec", "-T", "airflow", "python", "-c", code, check=False)

    assert attempt.returncode == 0, attempt.stderr


def test_airflow_and_the_api_share_the_sqlalchemy_version() -> None:
    code = "import sqlalchemy; print(sqlalchemy.__version__)"

    versions = {service: compose("exec", "-T", service, "python", "-c", code).stdout.strip() for service in ("airflow", "api")}

    assert versions["airflow"] == versions["api"], versions


def test_the_week_1_dag_runs() -> None:
    run = compose("exec", "-T", "airflow", "airflow", "dags", "test", "hello_world_week1", check=False)

    assert "state=success" in run.stdout + run.stderr, run.stdout + run.stderr


def airflow_login(password: str) -> str:
    with httpx.Client(base_url="http://localhost:8080") as airflow:
        csrf_token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', airflow.get("/auth/login/").text)
        assert csrf_token, "the login form has no CSRF token"
        response = airflow.post(
            "/auth/login/", data={"username": "admin", "password": password, "csrf_token": csrf_token.group(1)}
        )
    return response.headers["location"]


def test_airflow_rejects_admin_admin_and_accepts_the_generated_password() -> None:
    assert "/login" in airflow_login("admin")
    assert "/login" not in airflow_login(ENV["AIRFLOW_ADMIN_PASSWORD"])


def test_airflow_hides_its_configuration() -> None:
    value = compose("exec", "-T", "airflow", "airflow", "config", "get-value", "api", "expose_config")

    assert value.stdout.strip().lower() == "false"


def test_opensearch_refuses_plain_http() -> None:
    with pytest.raises(httpx.TransportError):
        httpx.get("http://localhost:9200/")


def test_opensearch_requires_a_password() -> None:
    assert httpx.get(OPENSEARCH_URL, verify=CERTIFICATE_AUTHORITY).status_code == 401


def test_opensearch_certificate_verifies_against_our_authority() -> None:
    response = httpx.get(f"{OPENSEARCH_URL}/_cluster/health", auth=APP_LOGIN, verify=CERTIFICATE_AUTHORITY)

    assert response.status_code == 200


def test_the_application_user_only_controls_the_project_indices() -> None:
    with httpx.Client(base_url=OPENSEARCH_URL, auth=APP_LOGIN, verify=CERTIFICATE_AUTHORITY) as opensearch:
        assert opensearch.put("/not-this-project").status_code == 403
        try:
            created = opensearch.put("/arxiv-papers-smoke-test")
            assert created.status_code == 200, created.text
        finally:
            opensearch.delete("/arxiv-papers-smoke-test")


def test_dashboards_requires_a_login() -> None:
    assert httpx.get("http://localhost:5601/app/home").status_code == 302
