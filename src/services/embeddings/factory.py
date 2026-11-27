import httpx
from src.config import Settings, get_settings

from .jina_client import JinaEmbeddingsClient


# The course defines this factory twice, also as make_embeddings_client, which its indexing calls. This repository keeps
# one, the API's (README.md).
def make_embeddings_service(
    settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None
) -> JinaEmbeddingsClient:
    """Factory function to create embeddings service.

    Creates a new client instance each time to avoid closed client issues.

    :param settings: Optional settings instance
    :param transport: None reaches Jina over the network. Tests pass a transport that fakes Jina.
    :returns: JinaEmbeddingsClient instance
    """
    if settings is None:
        settings = get_settings()

    # Get API key from settings
    api_key = settings.jina_api_key

    return JinaEmbeddingsClient(api_key=api_key, transport=transport, dimensions=settings.opensearch.vector_dimension)
