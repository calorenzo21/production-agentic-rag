"""Jina AI's embeddings API over HTTP, faked for the Jina client, which the indexing tests and the API tests share."""

import asyncio
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


def halfway(first: Sequence[float], second: Sequence[float]) -> list[float]:
    """The unit vector halfway between two unit vectors: a meaning near each, though less near than each is to itself."""
    total = [a + b for a, b in zip(first, second, strict=True)]
    length = math.hypot(*total)
    return [component / length for component in total]


class _Transport(httpx.MockTransport):
    """The network between a Jina client and the fake Jina. It tells when the client closes it."""

    closed = False

    async def aclose(self) -> None:
        self.closed = True


class FakeJina:
    """
    Jina AI's embeddings API over HTTP. It embeds each text as the vector the test chose for the texts that contain some
    words, or else as the text's own vector, the same every time. It fails the requests the test makes it fail, or
    rate-limits every request for as long as the test says, and keeps the body and time of each request it received,
    failed or not. It accepts any key, as no test has a real one.
    """

    def __init__(self) -> None:
        # The vector chosen for the texts that contain each words, in the order chosen. The first that matches wins.
        self.chosen_vectors: list[tuple[str, list[float]]] = []
        self.next_failures: deque[Failure] = deque()
        # The failures of every request from now on, or only of those embedding a text that contains the words given.
        self.lasting_failures: list[tuple[Failure, str | None]] = []
        # Until when, on the event loop's clock, every request is rate-limited, and whether each rate limit says how long.
        self.rate_limited_until: float | None = None
        self.says_how_long = False
        self.requests: list[dict[str, Any]] = []
        # When each request came, on the event loop's clock.
        self.request_times: list[float] = []
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

    def rate_limits_for(self, seconds: float, *, saying_how_long: bool = False) -> None:
        """
        Rate-limit every request for so many seconds from now, on the event loop's clock, which the tests can make
        virtual, as Jina does once a key has spent its tokens for the minute. Saying how long, each rate limit tells the
        whole seconds left in its Retry-After header, which Jina's documentation does not promise.
        """
        self.rate_limited_until = asyncio.get_running_loop().time() + seconds
        self.says_how_long = saying_how_long

    @property
    def closed(self) -> bool:
        """Whether a client that reaches it through its transport was closed, which closes the client's connections."""
        return self.transport.closed

    def _respond(self, request: httpx.Request) -> httpx.Response:
        if (request.method, request.url.path) != ("POST", "/v1/embeddings"):
            return httpx.Response(404, json={"detail": "Not Found"})
        body = json.loads(request.content)
        self.requests.append(body)
        self.request_times.append(asyncio.get_running_loop().time())
        texts: list[str] = body["input"]
        rate_limit_left = self._rate_limit_left()
        failure = Failure.RATE_LIMIT if rate_limit_left > 0 else self._failure_for(texts)
        if failure is Failure.TIMEOUT:
            raise httpx.ReadTimeout("Jina did not answer in time", request=request)
        if failure is not None:
            status, detail = FAILED_RESPONSES[failure]
            says_how_long = rate_limit_left > 0 and self.says_how_long
            headers = {"Retry-After": str(math.ceil(rate_limit_left))} if says_how_long else {}
            return httpx.Response(status, json={"detail": detail}, headers=headers)
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

    def _rate_limit_left(self) -> float:
        """How many seconds are left of the rate limit, on the event loop's clock: none once it is over."""
        if self.rate_limited_until is None:
            return 0
        return self.rate_limited_until - asyncio.get_running_loop().time()

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
