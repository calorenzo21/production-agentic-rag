import os
import subprocess
import sys

import pytest

COMPLETE_ENVIRONMENT = {
    "POSTGRES__HOST": "127.0.0.1",
    "POSTGRES__USER": "curator",
    "POSTGRES__PASSWORD": "not-a-real-password",
    "POSTGRES__DATABASE": "paper_curator",
    "OPENSEARCH__URL": "http://127.0.0.1:9",
    "OPENSEARCH__USERNAME": "curator",
    "OPENSEARCH__PASSWORD": "not-a-real-password",
    "OLLAMA__URL": "http://127.0.0.1:9",
}


def start_api(environment: dict[str, str]) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "--factory",
            "paper_curator.main:create_app",
            "--port",
            "0",
        ],
        env={"PATH": os.environ["PATH"], **environment},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def test_api_starts_when_every_required_value_is_present() -> None:
    process = start_api(COMPLETE_ENVIRONMENT)
    try:
        assert process.stdout is not None
        for line in process.stdout:
            if "Application startup complete" in line:
                break
        assert process.poll() is None
    finally:
        process.terminate()
        process.wait(timeout=10)


@pytest.mark.parametrize(
    ("missing", "reported_as"),
    [
        ("POSTGRES__PASSWORD", "postgres.password"),
        ("OPENSEARCH__PASSWORD", "opensearch.password"),
        ("POSTGRES__HOST", "postgres.host"),
        ("OLLAMA__URL", "ollama"),
    ],
)
def test_api_stops_at_startup_when_a_required_value_is_missing(
    missing: str, reported_as: str
) -> None:
    environment = {k: v for k, v in COMPLETE_ENVIRONMENT.items() if k != missing}

    process = start_api(environment)
    output, _ = process.communicate(timeout=30)

    assert process.returncode != 0
    assert reported_as in output
    assert "not-a-real-password" not in output


def test_api_stops_at_startup_when_a_secret_is_empty() -> None:
    process = start_api({**COMPLETE_ENVIRONMENT, "POSTGRES__PASSWORD": ""})
    output, _ = process.communicate(timeout=30)

    assert process.returncode != 0
    assert "postgres.password" in output
