import asyncio
import logging
from types import TracebackType
from typing import Self

import httpx
from src.schemas.embeddings.jina import JinaEmbeddingRequest, JinaEmbeddingResponse

logger = logging.getLogger(__name__)

# A call that embeds texts for indexing is attempted up to MAX_ATTEMPTS times. Between attempts it waits as long as
# Jina asks, or else FIRST_WAIT_SECONDS, doubled after each attempt, but never more than LONGEST_WAIT_SECONDS at a time.
# When Jina does not ask, that makes 7 waits of about 2 minutes in all, longer than the minute over which Jina counts a
# free key's 100,000 tokens.
MAX_ATTEMPTS = 8
FIRST_WAIT_SECONDS = 1.0
LONGEST_WAIT_SECONDS = 60.0


class JinaEmbeddingsClient:
    """Client for Jina AI embeddings API.

    Uses Jina embeddings v3 model with 1024 dimensions optimized for retrieval.
    Documentation: https://jina.ai/embeddings
    """

    def __init__(
        self, api_key: str, base_url: str = "https://api.jina.ai/v1", transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        """Initialize Jina embeddings client.

        :param api_key: Jina API key
        :param base_url: API base URL
        :param transport: None sends requests over the network. Tests pass a transport that fakes Jina.
        """
        self.api_key = api_key
        self.base_url = base_url
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self.client = httpx.AsyncClient(timeout=30.0, transport=transport)
        logger.info("Jina embeddings client initialized")

    async def embed_passages(self, texts: list[str], batch_size: int = 100) -> list[list[float]]:
        """Embed text passages for indexing.

        Unlike the course's, each call retries rate limits, server errors and timeouts, as a free key's limit makes rate
        limits certain during a day's indexing (README.md). The retries are the indexing's: embed_query, for Searches,
        makes one attempt.

        :param texts: List of text passages to embed
        :param batch_size: Number of texts to process in each API call
        :returns: List of embedding vectors
        """
        embeddings: list[list[float]] = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]

            request_data = JinaEmbeddingRequest(
                model="jina-embeddings-v3", task="retrieval.passage", dimensions=1024, input=batch
            )

            try:
                response = await self._post_retrying(request_data)

                result = JinaEmbeddingResponse(**response.json())
                batch_embeddings = [item["embedding"] for item in result.data]
                embeddings.extend(batch_embeddings)

                logger.debug(f"Embedded batch of {len(batch)} passages")

            except httpx.HTTPError as e:
                logger.error(f"Error embedding passages: {e}")
                raise
            except Exception as e:
                logger.error(f"Unexpected error in embed_passages: {e}")
                raise

        logger.info(f"Successfully embedded {len(texts)} passages")
        return embeddings

    async def _post_retrying(self, request_data: JinaEmbeddingRequest) -> httpx.Response:
        """
        Post a request to embed texts to Jina, and return its successful response. A transient failure is retried, up to
        MAX_ATTEMPTS attempts in all. Raises the last one, or the first failure that is not transient.
        """
        attempt = 1
        while True:
            try:
                response = await self.client.post(
                    f"{self.base_url}/embeddings", headers=self.headers, json=request_data.model_dump()
                )
                return response.raise_for_status()
            except (httpx.HTTPStatusError, httpx.TimeoutException) as e:
                if attempt == MAX_ATTEMPTS or not _is_transient(e):
                    raise
                # On the event loop's clock, which the tests make virtual.
                wait = _wait_before_retry(e, attempt)
                logger.warning(
                    f"Jina failed to embed {len(request_data.input)} texts, attempt {attempt} of {MAX_ATTEMPTS}, so "
                    f"retrying in {wait:g} seconds: {e}"
                )
                await asyncio.sleep(wait)
                attempt += 1

    async def embed_query(self, query: str) -> list[float]:
        """Embed a search query.

        :param query: Query text to embed
        :returns: Embedding vector for the query
        """
        request_data = JinaEmbeddingRequest(model="jina-embeddings-v3", task="retrieval.query", dimensions=1024, input=[query])

        try:
            response = await self.client.post(f"{self.base_url}/embeddings", headers=self.headers, json=request_data.model_dump())
            response.raise_for_status()

            result = JinaEmbeddingResponse(**response.json())
            embedding: list[float] = result.data[0]["embedding"]

            logger.debug(f"Embedded query: '{query[:50]}...'")
            return embedding

        except httpx.HTTPError as e:
            logger.error(f"Error embedding query: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error in embed_query: {e}")
            raise

    async def close(self) -> None:
        """Close the HTTP client."""
        await self.client.aclose()

    async def __aenter__(self) -> Self:
        """Async context manager entry."""
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: TracebackType | None
    ) -> None:
        """Async context manager exit."""
        await self.close()


def _wait_before_retry(failure: httpx.HTTPStatusError | httpx.TimeoutException, attempt: int) -> float:
    """
    How many seconds to wait after a failed attempt before the next: as long as Jina asks in the failure's Retry-After
    header, in whole seconds, or else FIRST_WAIT_SECONDS, doubled after each attempt. Never more than
    LONGEST_WAIT_SECONDS, so that a header gone wrong cannot stall the indexing: Jina is asked again then.
    """
    if isinstance(failure, httpx.HTTPStatusError):
        # A date, which RFC 9110 also allows, is left to the backoff, and so is a wait of none, which would spend every
        # attempt at once.
        retry_after = failure.response.headers.get("Retry-After", "")
        if retry_after.isdecimal() and int(retry_after) > 0:
            return min(float(retry_after), LONGEST_WAIT_SECONDS)
    return min(FIRST_WAIT_SECONDS * 2.0 ** (attempt - 1), LONGEST_WAIT_SECONDS)


def _is_transient(failure: httpx.HTTPStatusError | httpx.TimeoutException) -> bool:
    """
    Whether the failure may pass if the call is retried: a timeout, a rate limit or a server error. A retry would not
    make a request valid, nor an invalid key or a spent balance good again.
    """
    if isinstance(failure, httpx.TimeoutException):
        return True
    status = failure.response.status_code
    return status == httpx.codes.TOO_MANY_REQUESTS or httpx.codes.is_server_error(status)
