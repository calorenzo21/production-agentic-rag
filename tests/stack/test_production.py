import json
import re
import subprocess

import httpx
import pytest

from tests.stack.support import SECRETS, Stack, psql_over_tcp, run_compose, services

LONG_RUNNING = {
    "caddy",
    "postgres",
    "opensearch",
    "opensearch-dashboards",
    "api",
    "airflow-apiserver",
    "airflow-scheduler",
    "airflow-dag-processor",
    "airflow-triggerer",
}


@pytest.fixture(autouse=True)
def production_only(stack: Stack) -> None:
    if stack.mode != "prod":
        pytest.skip("production mode only")


def configuration() -> dict[str, dict[str, object]]:
    rendered = run_compose("config", "--format", "json").stdout
    services_config: dict[str, dict[str, object]] = json.loads(rendered)["services"]
    return services_config


def test_the_reverse_proxy_is_the_only_exposed_entry_point() -> None:
    published = {
        (service["Service"], publisher["PublishedPort"])
        for service in services()
        for publisher in service["Publishers"]
        if publisher["PublishedPort"]
    }

    assert published == {("caddy", 80), ("caddy", 443)}


def test_plain_http_redirects_to_https() -> None:
    response = httpx.get("http://localhost/api/v1/health/live")

    assert response.status_code == 308
    assert response.headers["location"].startswith("https://localhost")


@pytest.mark.parametrize("service", sorted(LONG_RUNNING))
def test_containers_run_as_non_root(service: str) -> None:
    uid = run_compose("exec", "-T", service, "id", "-u").stdout.strip()

    assert uid not in ("", "0")


def test_secrets_do_not_show_up_in_container_inspection() -> None:
    names = [service["Name"] for service in services()]
    inspection = subprocess.run(
        ["docker", "inspect", *names], check=True, capture_output=True, text=True
    )
    values = [
        path.read_text().strip()
        for path in SECRETS.iterdir()
        if path.suffix == "" and path.name != "airflow_database_url"
    ]

    assert values
    for value in values:
        assert value not in inspection.stdout


def test_postgres_refuses_a_connection_without_a_password() -> None:
    attempt = psql_over_tcp("curator", None, "paper_curator", "SELECT 1")

    assert attempt.returncode != 0
    assert "no password supplied" in attempt.stderr


def test_postgres_refuses_a_wrong_password() -> None:
    attempt = psql_over_tcp("curator", "wrong-password", "paper_curator", "SELECT 1")

    assert attempt.returncode != 0
    assert "password authentication failed" in attempt.stderr


def test_opensearch_refuses_a_request_without_credentials() -> None:
    status = run_compose(
        "exec",
        "-T",
        "opensearch",
        "curl",
        "--silent",
        "--output",
        "/dev/null",
        "--write-out",
        "%{http_code}",
        "--cacert",
        "config/certs/ca.crt",
        "https://localhost:9200/",
    ).stdout

    assert status == "401"


def test_airflow_requires_a_login_through_the_proxy(stack: Stack) -> None:
    response = httpx.get(f"{stack.airflow_url}/api/v2/dags", verify=stack.verify)

    assert response.status_code == 401


def test_airflow_ui_is_served_through_the_proxy(stack: Stack) -> None:
    response = httpx.get(f"{stack.airflow_url}/", verify=stack.verify, follow_redirects=True)

    assert response.status_code == 200


def test_dashboards_requires_a_login_through_the_proxy(stack: Stack) -> None:
    response = httpx.get(stack.dashboards_url, verify=stack.verify, follow_redirects=True)

    assert "/app/login" in str(response.url)


def test_dashboards_accepts_the_generated_login(stack: Stack) -> None:
    password = (SECRETS / "opensearch_admin_password").read_text().strip()

    response = httpx.post(
        f"{stack.dashboards_url}/auth/login",
        json={"username": "admin", "password": password},
        headers={"osd-xsrf": "true"},
        verify=stack.verify,
    )

    assert response.status_code == 200


def test_dashboards_rejects_a_wrong_login(stack: Stack) -> None:
    response = httpx.post(
        f"{stack.dashboards_url}/auth/login",
        json={"username": "admin", "password": "admin"},
        headers={"osd-xsrf": "true"},
        verify=stack.verify,
    )

    assert response.status_code == 401


def test_health_response_reveals_no_versions_or_hostnames(stack: Stack) -> None:
    response = httpx.get(f"{stack.api_url}/api/v1/health", verify=stack.verify, timeout=10)

    assert "server" not in response.headers
    assert set(response.json()) == {"status", "dependencies"}


@pytest.mark.parametrize("service", ["api", "caddy"])
def test_logs_are_structured(service: str, stack: Stack) -> None:
    httpx.get(f"{stack.api_url}/api/v1/health/live", verify=stack.verify)

    lines = run_compose("logs", "--no-log-prefix", service).stdout.splitlines()

    assert lines
    for line in lines:
        json.loads(line)


@pytest.mark.parametrize(
    "service",
    ["postgres", "opensearch", "opensearch-dashboards", "airflow-scheduler"],
)
def test_services_with_startup_noise_still_log_structured_lines(service: str) -> None:
    lines = run_compose("logs", "--no-log-prefix", service).stdout.splitlines()

    assert any(line.startswith("{") and json.loads(line) for line in lines)


def test_images_are_pinned_to_exact_versions() -> None:
    images = run_compose("config", "--images").stdout.split()
    upstream = [image for image in images if not image.endswith(":local")]

    assert upstream
    for image in upstream:
        assert re.search(r":\d+\.\d+", image), image


def test_long_running_services_have_limits_and_restart_policies() -> None:
    config = configuration()

    for name in LONG_RUNNING:
        assert config[name].get("mem_limit"), name
        assert config[name].get("restart") == "unless-stopped", name
