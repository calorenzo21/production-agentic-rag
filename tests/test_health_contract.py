from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from paper_curator.health.checks import Checks, get_checks
from paper_curator.main import create_app
from tests.support import unreachable_settings


async def up() -> bool:
    return True


async def down() -> bool:
    return False


async def broken() -> bool:
    raise ConnectionError("db.internal:5432 refused the connection")


def client_with(checks: Checks) -> TestClient:
    app = create_app(unreachable_settings())
    app.dependency_overrides[get_checks] = lambda: checks
    return TestClient(app)


def test_liveness_only_confirms_the_process_is_responding() -> None:
    client = client_with(Checks(postgres=down, opensearch=down, ollama=down))

    response = client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_is_ok_when_every_dependency_is_up() -> None:
    client = client_with(Checks(postgres=up, opensearch=up, ollama=up))

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "dependencies": {"postgres": "ok", "opensearch": "ok", "ollama": "ok"},
    }


def test_health_is_degraded_with_a_200_when_only_ollama_is_down() -> None:
    client = client_with(Checks(postgres=up, opensearch=up, ollama=down))

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "degraded",
        "dependencies": {"postgres": "ok", "opensearch": "ok", "ollama": "down"},
    }


@pytest.mark.parametrize(
    ("checks", "dependencies"),
    [
        (
            Checks(postgres=down, opensearch=up, ollama=up),
            {"postgres": "down", "opensearch": "ok", "ollama": "ok"},
        ),
        (
            Checks(postgres=up, opensearch=down, ollama=up),
            {"postgres": "ok", "opensearch": "down", "ollama": "ok"},
        ),
        (
            Checks(postgres=down, opensearch=down, ollama=down),
            {"postgres": "down", "opensearch": "down", "ollama": "down"},
        ),
    ],
)
def test_health_is_unhealthy_with_a_503_when_postgres_or_opensearch_is_down(
    checks: Checks, dependencies: dict[str, str]
) -> None:
    client = client_with(checks)

    response = client.get("/api/v1/health")

    assert response.status_code == 503
    assert response.json() == {"status": "unhealthy", "dependencies": dependencies}


def test_a_failing_check_counts_as_down_and_reveals_nothing_about_the_failure() -> None:
    client = client_with(Checks(postgres=broken, opensearch=up, ollama=up))

    response = client.get("/api/v1/health")

    assert response.status_code == 503
    assert response.json()["dependencies"]["postgres"] == "down"
    assert "db.internal" not in response.text


@pytest.fixture
def real_checks_client() -> Iterator[TestClient]:
    with TestClient(create_app(unreachable_settings())) as client:
        yield client


def test_real_checks_report_unreachable_dependencies_as_down(
    real_checks_client: TestClient,
) -> None:
    response = real_checks_client.get("/api/v1/health")

    assert response.status_code == 503
    assert response.json() == {
        "status": "unhealthy",
        "dependencies": {"postgres": "down", "opensearch": "down", "ollama": "down"},
    }


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_the_api_exposes_only_health_endpoints(path: str) -> None:
    client = client_with(Checks(postgres=up, opensearch=up, ollama=up))

    assert client.get(path).status_code == 404
