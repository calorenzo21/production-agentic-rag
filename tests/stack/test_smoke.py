"""Checks a running stack: `make start`, then `make smoke`.

These tests guard what the course does not give us: its services working together, and every
security Deviation. README.md lists them.
"""

import json
import re
import ssl
import subprocess
import tomllib
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


def test_the_api_reaches_the_database_opensearch_and_native_ollama() -> None:
    response = httpx.get("http://localhost:8000/api/v1/health", timeout=10)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok", body
    assert body["services"]["database"]["status"] == "healthy"
    # OpenSearch only answers over TLS, to a user with a password, so this proves the API connects over TLS as rag_app.
    assert body["services"]["opensearch"]["status"] == "healthy"
    assert body["services"]["ollama"]["status"] == "healthy"


def test_the_api_reports_the_chunk_index_and_its_chunks() -> None:
    opensearch = settings_in("api", "OPENSEARCH__")
    chunk_index = f"{opensearch['OPENSEARCH__INDEX_NAME']}-{opensearch['OPENSEARCH__CHUNK_INDEX_SUFFIX']}"
    held = httpx.get(f"{OPENSEARCH_URL}/{chunk_index}/_count", auth=APP_LOGIN, verify=CERTIFICATE_AUTHORITY).json()["count"]

    response = httpx.get("http://localhost:8000/api/v1/health", timeout=10)

    # Each of the chunk index's documents is a Chunk, as the course reports them.
    assert response.json()["services"]["opensearch"]["message"] == f"Index '{chunk_index}' with {held} documents"


def test_a_real_search_answers_by_words_and_meaning_with_papers_rag_db_holds() -> None:
    response = httpx.post("http://localhost:8000/api/v1/hybrid-search/", json={"query": "language models"}, timeout=10)

    # The API reaches OpenSearch over TLS as rag_app, and OpenSearch takes its query over the chunk index's mapping.
    assert response.status_code == 200, response.text
    found = response.json()
    # Not by words alone, which the API falls back to when Jina fails, so the API's Jina key embedded the query, and
    # OpenSearch fused the two halves with the RRF pipeline, which the API created as rag_app.
    assert found["search_mode"] == "hybrid"
    held = set(superuser_sql("rag_db", "SELECT arxiv_id FROM papers").splitlines())
    assert {hit["arxiv_id"] for hit in found["hits"]} <= held


def test_the_api_lists_the_papers_rag_db_holds() -> None:
    response = httpx.get("http://localhost:8000/api/v1/papers/", params={"limit": 100}, timeout=10)

    assert response.status_code == 200, response.text
    held = set(superuser_sql("rag_db", "SELECT arxiv_id FROM papers").splitlines())
    listed = response.json()
    assert listed["total"] == len(held)
    assert len(listed["papers"]) == min(len(held), 100)
    assert {paper["arxiv_id"] for paper in listed["papers"]} <= held


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


def installed_version(service: str, package: str) -> str | None:
    """The version of a package installed in a service's container, or None if it is not installed."""
    code = (
        "import importlib.metadata as metadata, sys\n"
        "try: print(metadata.version(sys.argv[1]))\n"
        "except metadata.PackageNotFoundError: pass"
    )
    return compose("exec", "-T", service, "python", "-c", code, package).stdout.strip() or None


@pytest.mark.parametrize("package", ["sqlalchemy", "pydantic"])
def test_airflow_and_the_api_share_the_version_of(package: str) -> None:
    versions = {service: installed_version(service, package) for service in ("airflow", "api")}

    assert versions["airflow"] is not None
    assert versions["airflow"] == versions["api"], versions


def locked_version(package: str) -> str | None:
    lock = tomllib.loads((REPOSITORY / "uv.lock").read_text())
    return next((entry["version"] for entry in lock["package"] if entry["name"] == package), None)


@pytest.mark.parametrize("package", ["docling", "opensearch-py", "httpx"])
def test_airflow_runs_the_locked_version_of(package: str) -> None:
    version = installed_version("airflow", package)

    assert version is not None
    assert version == locked_version(package)


def can_import(service: str, module: str) -> bool:
    # Checks the import, not the distribution: the `docling` distribution only depends on `docling-slim`, which has the code.
    code = "import importlib.util, sys; print(importlib.util.find_spec(sys.argv[1]) is not None)"
    return compose("exec", "-T", service, "python", "-c", code, module).stdout.strip() == "True"


def test_the_api_image_has_no_docling() -> None:
    assert not can_import("api", "docling")


def test_airflow_imports_every_dag() -> None:
    # --local parses the DAGs now instead of reading what the DAG processor last recorded. The bundle puts the DAGs
    # folder on the path, as the DAG processor does.
    errors = compose(
        "exec", "-T", "airflow", "airflow", "dags", "list-import-errors", "--local", "--bundle-name", "dags-folder", check=False
    )

    assert errors.returncode == 0, errors.stdout + errors.stderr


def environment(container: str) -> dict[str, str]:
    """The environment a container was created with, which every process in it receives.

    docker inspect reads it from outside, so it also covers the containers without Python, and migrate's, which has exited.
    It holds secrets, which a failed assertion on it would print.
    """
    inspected = subprocess.run(
        ["docker", "inspect", "--format", "{{json .Config.Env}}", container], check=True, capture_output=True, text=True
    )
    return dict(variable.split("=", 1) for variable in json.loads(inspected.stdout))


def settings_in(service: str, prefix: str) -> dict[str, str]:
    return {name: value for name, value in environment(services()[service]["ID"]).items() if name.startswith(prefix)}


@pytest.mark.parametrize("prefix", ["ARXIV__", "PDF_PARSER__"])
def test_airflow_alone_receives_the_ingestion_settings_from_env(prefix: str) -> None:
    # A setting missing or empty in .env takes compose.yml's default, which is the value .env.example gives it.
    example = dotenv_values(REPOSITORY / ".env.example")
    expected = {name: ENV.get(name) or value for name, value in example.items() if name.startswith(prefix)}

    assert expected
    assert settings_in("airflow", prefix) == expected
    assert settings_in("api", prefix) == {}


def test_the_api_and_airflow_alone_receive_the_opensearch_settings() -> None:
    expected = {name for name in dotenv_values(REPOSITORY / ".env.example") if name.startswith("OPENSEARCH__")}

    assert expected
    for name, service in services().items():
        received = {variable for variable in environment(service["ID"]) if variable.startswith("OPENSEARCH__")}
        assert received == (expected if name in {"api", "airflow"} else set()), name


# The indexing's settings that hold no secret: the chunker's, and the chunk index's and its vectors'.
INDEXING_SETTINGS = (
    "CHUNKING__",
    "OPENSEARCH__CHUNK_INDEX_SUFFIX",
    "OPENSEARCH__VECTOR_DIMENSION",
    "OPENSEARCH__VECTOR_SPACE_TYPE",
)


def test_the_api_and_airflow_alone_receive_the_indexing_settings_from_env() -> None:
    # A setting missing or empty in .env takes compose.yml's default, which is the value .env.example gives it.
    example = dotenv_values(REPOSITORY / ".env.example")
    expected = {name: ENV.get(name) or value for name, value in example.items() if name.startswith(INDEXING_SETTINGS)}

    assert len(expected) == 7
    for name, service in services().items():
        received = {
            variable: value for variable, value in environment(service["ID"]).items() if variable.startswith(INDEXING_SETTINGS)
        }
        # Both run the indexing: Airflow each day, and the API's container for make reindex.
        assert received == (expected if name in {"api", "airflow"} else {}), name


def test_the_api_and_airflow_alone_receive_the_jina_key_from_env() -> None:
    key = ENV.get("JINA_API_KEY")
    paste_your_key = "make setup cannot generate the Jina AI key: paste yours into .env"
    assert key, paste_your_key
    assert key != dotenv_values(REPOSITORY / ".env.example")["JINA_API_KEY"], paste_your_key
    environments = {name: environment(service["ID"]) for name, service in services().items()}

    # Service names only, as the key is a secret, which a failed assertion on it would print.
    receiving_a_key = {name for name, variables in environments.items() if "JINA_API_KEY" in variables}
    receiving_the_key = {name for name, variables in environments.items() if variables.get("JINA_API_KEY") == key}
    assert receiving_a_key == receiving_the_key == {"api", "airflow"}


def test_no_container_receives_the_former_flat_opensearch_settings() -> None:
    former = {"OPENSEARCH_HOST", "OPENSEARCH_USERNAME", "OPENSEARCH_PASSWORD", "OPENSEARCH_CA_CERTS"}

    for name, service in services().items():
        received = environment(service["ID"]).keys() & former
        assert not received, name


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


# Demo logins from the image's own internal_users.yml. Its demo setup changes admin's password but keeps kibanaserver's.
@pytest.mark.parametrize("user", ["admin", "kibanaserver"])
def test_opensearch_rejects_the_demo_login(user: str) -> None:
    assert httpx.get(OPENSEARCH_URL, auth=(user, user), verify=CERTIFICATE_AUTHORITY).status_code == 401


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
