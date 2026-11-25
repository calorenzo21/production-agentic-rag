"""Jina AI's embeddings API over HTTP, faked for the Jina client, which the indexing tests and the API tests share."""

import hashlib
import json
import math
from collections import deque
from collections.abc import Sequence
from enum import Enum
from typing import Any

import httpx


class Failure(Enum):
    """A way Jina fails a request."""

    RATE_LIMIT = "rate limit"
    SERVER_ERROR = "server error"
    TIMEOUT = "timeout"
    INVALID_KEY = "invalid key"


def own_vector(text: str, dimensions: int) -> list[float]:
    """
    The unit vector Jina embeds a text as, unless the test chose one: the text alone decides it, and it is nearly
    orthogonal to any other text's, so that no two texts mean the same by chance. Jina's own vectors are unit vectors too.
    """
    digest = hashlib.shake_256(text.encode()).digest(2 * dimensions)
    components = [int.from_bytes(digest[start : start + 2]) - 32767.5 for start in range(0, len(digest), 2)]
    length = math.hypot(*components)
    return [component / length for component in components]


class _Transport(httpx.MockTransport):
    """The network between a Jina client and the fake Jina. It tells when the client closes it."""

    closed = False

    async def aclose(self) -> None:
        self.closed = True


class FakeJina:
    """
    Jina AI's embeddings API over HTTP. It embeds each text as the vector the test chose for the texts that contain some
    words, or else as the text's own vector, the same every time. It fails the requests the test makes it fail, and
    keeps the body of each request it received, failed or not. It accepts any key, as no test has a real one.
    """

    def __init__(self) -> None:
        # The vector chosen for the texts that contain each words, in the order chosen. The first that matches wins.
        self.chosen_vectors: list[tuple[str, list[float]]] = []
        self.next_failures: deque[Failure] = deque()
        # The failures of every request from now on, or only of those embedding a text that contains the words given.
        self.lasting_failures: list[tuple[Failure, str | None]] = []
        self.requests: list[dict[str, Any]] = []
        self.transport = _Transport(self._respond)

    def embeds(self, words: str, *, as_vector: Sequence[float]) -> None:
        """Embed each text that contains the words as the vector, whatever else the text contains."""
        self.chosen_vectors.append((words, list(as_vector)))

    def fails_next(self, *failures: Failure) -> None:
        """Fail the next requests, one failure each, in order, then answer again."""
        self.next_failures.extend(failures)

    def keeps_failing(self, failure: Failure, *, on: str | None = None) -> None:
        """Fail every request from now on, or only those embedding a text that contains the words given."""
        self.lasting_failures.append((failure, on))

    @property
    def closed(self) -> bool:
        """Whether a client that reaches it through its transport was closed, which closes the client's connections."""
        return self.transport.closed

    def _respond(self, request: httpx.Request) -> httpx.Response:
        if (request.method, request.url.path) != ("POST", "/v1/embeddings"):
            return httpx.Response(404, json={"detail": "Not Found"})
        body = json.loads(request.content)
        self.requests.append(body)
        texts: list[str] = body["input"]
        failure = self._failure_for(texts)
        if failure is Failure.TIMEOUT:
            raise httpx.ReadTimeout("Jina did not answer in time", request=request)
        if failure is not None:
            status, detail = FAILED_RESPONSES[failure]
            return httpx.Response(status, json={"detail": detail})
        tokens = sum(len(text.split()) for text in texts)
        # jina-embeddings-v3 makes vectors of 1024 dimensions unless asked for fewer.
        dimensions = body.get("dimensions", 1024)
        return httpx.Response(
            200,
            json={
                "model": body["model"],
                "object": "list",
                "usage": {"total_tokens": tokens, "prompt_tokens": tokens},
                "data": [
                    {"object": "embedding", "index": index, "embedding": self._vector(text, dimensions)}
                    for index, text in enumerate(texts)
                ],
            },
        )

    def _failure_for(self, texts: list[str]) -> Failure | None:
        if self.next_failures:
            return self.next_failures.popleft()
        for failure, words in self.lasting_failures:
            if words is None or any(words in text for text in texts):
                return failure
        return None

    def _vector(self, text: str, dimensions: int) -> list[float]:
        for words, vector in self.chosen_vectors:
            if words in text:
                return vector
        return own_vector(text, dimensions)


# The status and detail Jina answers each failure with, but a timeout, for which it never answers.
FAILED_RESPONSES: dict[Failure, tuple[int, str]] = {
    Failure.RATE_LIMIT: (429, "Rate limit exceeded"),
    Failure.SERVER_ERROR: (500, "Internal Server Error"),
    Failure.INVALID_KEY: (401, "Invalid API key"),
}
