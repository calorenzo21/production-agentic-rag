import json
import os
import ssl
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

REPOSITORY = Path(__file__).parents[2]
SECRETS = REPOSITORY / "secrets"
PRODUCTION_COMPOSE_FILE = "compose.yaml:compose.prod.yaml"


def run_compose(
    *arguments: str, mode: str | None = None, check: bool = True
) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    if (mode or os.environ["STACK_MODE"]) == "prod":
        environment["COMPOSE_FILE"] = PRODUCTION_COMPOSE_FILE
    return subprocess.run(
        ["docker", "compose", *arguments],
        cwd=REPOSITORY,
        env=environment,
        check=check,
        capture_output=True,
        text=True,
    )


def services() -> list[dict[str, Any]]:
    output = run_compose("ps", "--all", "--format", "json").stdout
    return [json.loads(line) for line in output.splitlines()]


PSQL_OPTIONS = ("--tuples-only", "--no-align", "--set=ON_ERROR_STOP=1")


def psql(database: str, sql: str) -> subprocess.CompletedProcess[str]:
    """Run SQL as the superuser, over the socket inside the container."""
    return run_compose(
        "exec",
        "-T",
        "--user=postgres",
        "postgres",
        "psql",
        "--username=postgres",
        f"--dbname={database}",
        *PSQL_OPTIONS,
        f"--command={sql}",
        check=False,
    )


def psql_over_tcp(
    user: str, password: str | None, database: str, sql: str
) -> subprocess.CompletedProcess[str]:
    """Run SQL the way a service connects: over TCP, with its own user and password."""
    return run_compose(
        "exec",
        "-T",
        *([f"--env=PGPASSWORD={password}"] if password else []),
        "postgres",
        "psql",
        "--host=127.0.0.1",
        "--no-password",
        f"--username={user}",
        f"--dbname={database}",
        *PSQL_OPTIONS,
        f"--command={sql}",
        check=False,
    )


def read_env_file(name: str) -> str:
    for line in (REPOSITORY / ".env").read_text().splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1]
    raise KeyError(name)


@dataclass(frozen=True)
class Stack:
    mode: Literal["dev", "prod"]
    api_url: str
    airflow_url: str
    dashboards_url: str
    airflow_password: str
    curator_db_password: str
    airflow_db_password: str
    verify: ssl.SSLContext | bool


def development_stack() -> Stack:
    return Stack(
        mode="dev",
        api_url="http://127.0.0.1:8000",
        airflow_url="http://127.0.0.1:8080",
        dashboards_url="http://127.0.0.1:5601",
        airflow_password=read_env_file("AIRFLOW_ADMIN_PASSWORD"),
        curator_db_password=read_env_file("POSTGRES__PASSWORD"),
        airflow_db_password=read_env_file("AIRFLOW_DB_PASSWORD"),
        verify=True,
    )


def production_stack(directory: Path) -> Stack:
    root_certificate = directory / "root.crt"
    run_compose("cp", "caddy:/data/caddy/pki/authorities/local/root.crt", str(root_certificate))
    return Stack(
        mode="prod",
        api_url="https://localhost",
        airflow_url="https://airflow.localhost",
        dashboards_url="https://opensearch.localhost",
        airflow_password=(SECRETS / "airflow_admin_password").read_text().strip(),
        curator_db_password=(SECRETS / "curator_db_password").read_text().strip(),
        airflow_db_password=(SECRETS / "airflow_db_password").read_text().strip(),
        verify=ssl.create_default_context(cafile=root_certificate),
    )
