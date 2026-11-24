"""The Ollama client, against a local socket that stands in for an Ollama that never answers."""

import asyncio
import socket
from collections.abc import Iterator

import pytest
from src.config import Settings
from src.exceptions import OllamaTimeoutError
from src.services.ollama import OllamaClient


@pytest.fixture
def silent_ollama() -> Iterator[str]:
    """The URL of a server that accepts connections but never answers a request."""
    with socket.create_server(("127.0.0.1", 0)) as server:
        host, port = server.getsockname()
        yield f"http://{host}:{port}"


async def test_the_health_check_times_out_after_ollama_timeout(silent_ollama: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_HOST", silent_ollama)
    monkeypatch.setenv("OLLAMA_TIMEOUT", "1")
    ollama = OllamaClient(Settings())

    # Well before the 30 seconds the course's Week 2 hard-codes.
    async with asyncio.timeout(5):
        with pytest.raises(OllamaTimeoutError):
            await ollama.health_check()
