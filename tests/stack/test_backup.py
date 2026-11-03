import os
import shutil
import subprocess
from collections.abc import Iterator

import httpx
import pytest

from tests.stack.support import PRODUCTION_COMPOSE_FILE, REPOSITORY, Stack, psql

DATABASES = ("paper_curator", "airflow")


def run_script(*arguments: str) -> str:
    environment = dict(os.environ)
    if os.environ["STACK_MODE"] == "prod":
        environment["COMPOSE_FILE"] = PRODUCTION_COMPOSE_FILE
    result = subprocess.run(
        arguments, cwd=REPOSITORY, env=environment, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def read_marker(database: str) -> subprocess.CompletedProcess[str]:
    return psql(database, "SELECT note FROM backup_check")


@pytest.fixture
def markers() -> Iterator[None]:
    for database in DATABASES:
        created = psql(
            database,
            f"CREATE TABLE backup_check (note text);INSERT INTO backup_check VALUES ('{database}')",
        )
        assert created.returncode == 0, created.stderr
    yield
    for database in DATABASES:
        psql(database, "DROP TABLE IF EXISTS backup_check")


def test_restore_brings_back_the_backed_up_data(markers: None, stack: Stack) -> None:
    backup = run_script("scripts/backup.sh").splitlines()[-1]
    try:
        for database in DATABASES:
            assert psql(database, "DROP TABLE backup_check").returncode == 0
            assert read_marker(database).returncode != 0

        run_script("scripts/restore.sh", backup)

        for database in DATABASES:
            assert read_marker(database).stdout.strip() == database
    finally:
        shutil.rmtree(REPOSITORY / backup)

    health = httpx.get(f"{stack.api_url}/api/v1/health", verify=stack.verify, timeout=10)
    assert health.status_code == 200
