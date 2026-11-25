import logging
from typing import Any

from opensearchpy import OpenSearch, helpers
from opensearchpy.exceptions import NotFoundError, RequestError, TransportError
from src.config import Settings, get_settings
from src.exceptions import OpenSearchException

from .index_config_hybrid import arxiv_papers_chunks_mapping
from .query_builder import PaperQueryBuilder

logger = logging.getLogger(__name__)


class OpenSearchClient:
    """
    Client for OpenSearch operations including index management and search.

    This client provides methods for creating indices, indexing papers,
    searching with BM25 scoring, and managing OpenSearch cluster operations.
    """

    def __init__(self, host: str = "http://localhost:9200", settings: Settings | None = None):
        """Initialize OpenSearch client.

        :param host: OpenSearch cluster endpoint URL
        :param settings: Application settings instance (uses default if None)
        :type host: str
        :type settings: Optional[Settings]
        """
        self.host = host
        self.settings = settings or get_settings()
        self.index_name = f"{self.settings.opensearch.index_name}-{self.settings.opensearch.chunk_index_suffix}"
        # The course's OpenSearch has no security, so its client connects over plain HTTP without a login. This one
        # requires TLS, which an https host turns on: the client verifies OpenSearch's certificate against our CA and
        # logs in as rag_app. The tests' disposable OpenSearch has no security, so they reach it through an http host.
        self.client = OpenSearch(
            hosts=[host],
            http_auth=(self.settings.opensearch.username, self.settings.opensearch.password),
            verify_certs=True,
            ca_certs=self.settings.opensearch.ca_certs,
            http_compress=True,
        )
        logger.info(f"OpenSearch client initialized with host: {host}")

    def setup_indices(self, force: bool = False) -> dict[str, bool]:
        """Setup the hybrid search index."""
        results = {}
        results["hybrid_index"] = self._create_hybrid_index(force)
        return results

    def _create_hybrid_index(self, force: bool = False) -> bool:
        """Create hybrid index for all search types (BM25, vector, hybrid).

        Unlike the course's, it reports a failure by returning False, as Week 3's creation of the Index did, rather than
        raising it, so that the API still starts when OpenSearch fails it (README.md).

        :param force: If True, recreate index even if it exists
        :returns: True if created, False if it already exists or could not be created
        """
        try:
            # Check if index exists
            if self.client.indices.exists(index=self.index_name):
                if force:
                    logger.info(f"Deleting existing hybrid index: {self.index_name}")
                    self.client.indices.delete(index=self.index_name)
                else:
                    self._wait_for_existing_index()
                    return False

            # Create index with mappings
            try:
                mapping = arxiv_papers_chunks_mapping(
                    vector_dimension=self.settings.opensearch.vector_dimension,
                    vector_space_type=self.settings.opensearch.vector_space_type,
                )
                response = self.client.indices.create(index=self.index_name, body=mapping)
            except RequestError as e:
                # The API's 4 workers start at once, so another may have created the Index since the check above.
                if e.error != "resource_already_exists_exception":
                    raise
                self._wait_for_existing_index()
                return False

            if response.get("acknowledged"):
                logger.info(f"Created hybrid index: {self.index_name}")
                return True
            else:
                logger.error(f"Failed to create hybrid index: {response}")
                return False

        except RequestError as e:
            logger.error(f"Error creating hybrid index: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error creating hybrid index: {e}")
            return False

    def _wait_for_existing_index(self) -> None:
        """Wait until the existing Index can be used, as creating it does, since another worker may have just created it."""
        logger.info(f"Index {self.index_name} already exists")
        # Creating an index waits for its primary shard to start. A new index reports yellow before then.
        self.client.cluster.health(index=self.index_name, wait_for_active_shards=1)

    def index_exists(self) -> bool:
        """
        Whether the Index exists. Unlike the course's methods, it raises OpenSearchException when it cannot tell, as when
        OpenSearch does not answer, so that an OpenSearch that is down is not taken for a missing Index.
        """
        try:
            return bool(self.client.indices.exists(index=self.index_name))
        except TransportError as e:
            raise OpenSearchException(str(e)) from e

    def bulk_index_chunks(self, chunks: list[dict[str, Any]]) -> dict[str, int]:
        """Bulk index multiple chunks.

        Unlike the course's, it goes on past a Chunk OpenSearch rejects, and counts it. The course's raises at the first
        one, so its count of failed Chunks is always 0 (README.md). It still raises when OpenSearch does not answer.

        :param chunks: The Chunks' entries
        :returns: Statistics
        """
        try:
            actions = [{"_index": self.index_name, "_source": chunk} for chunk in chunks]

            success, failed = helpers.bulk(self.client, actions, refresh=True, raise_on_error=False)

            for failure in failed:
                # Each failure is keyed on its action, an index here, and also holds the whole Chunk, which is left out.
                (result,) = failure.values()
                logger.error(f"OpenSearch rejected a Chunk: {result.get('error')}")
            logger.info(f"Bulk indexed {success} chunks, {len(failed)} failed")
            return {"success": success, "failed": len(failed)}

        except Exception as e:
            logger.error(f"Bulk chunk indexing error: {e}")
            raise

    def delete_paper_chunks(self, arxiv_id: str) -> bool:
        """Delete all chunks for a specific paper.

        :param arxiv_id: ArXiv ID of the paper
        :returns: True if deletion was successful
        """
        try:
            response = self.client.delete_by_query(
                index=self.index_name, body={"query": {"term": {"arxiv_id": arxiv_id}}}, refresh=True
            )

            deleted: int = response.get("deleted", 0)
            logger.info(f"Deleted {deleted} chunks for paper {arxiv_id}")
            return deleted > 0

        except Exception as e:
            logger.error(f"Error deleting chunks: {e}")
            return False

    def search_papers(
        self,
        query: str,
        size: int = 10,
        from_: int = 0,
        fields: list[str] | None = None,
        categories: list[str] | None = None,
        track_total_hits: bool = True,
        latest_papers: bool = False,
    ) -> dict[str, Any]:
        """Search papers using BM25 scoring with query builder.

        :param query: Search query text
        :param size: Number of results to return
        :param from_: Offset for pagination
        :param fields: List of fields to search in (default: title, abstract, authors)
        :param categories: Filter by categories
        :param track_total_hits: Whether to track total hits accurately
        :param latest_papers: Sort by publication date instead of relevance
        :type query: str
        :type size: int
        :type from_: int
        :type fields: Optional[List[str]]
        :type categories: Optional[List[str]]
        :type track_total_hits: bool
        :type latest_papers: bool
        :returns: Search results with hits and metadata
        :rtype: Dict[str, Any]
        """
        try:
            # Build query using query builder
            query_builder = PaperQueryBuilder(
                query=query,
                size=size,
                from_=from_,
                fields=fields,
                categories=categories,
                track_total_hits=track_total_hits,
                latest_papers=latest_papers,
            )

            search_body = query_builder.build()

            # Execute search
            response = self.client.search(index=self.index_name, body=search_body)

            # Format results
            results = {"total": response["hits"]["total"]["value"], "hits": []}

            for hit in response["hits"]["hits"]:
                paper = hit["_source"]
                paper["score"] = hit["_score"]
                if "highlight" in hit:
                    paper["highlights"] = hit["highlight"]
                results["hits"].append(paper)

            logger.info(f"Search for '{query}' returned {results['total']} results")
            return results

        except NotFoundError:
            logger.error(f"Index {self.index_name} not found")
            return {"total": 0, "hits": [], "error": "Index not found"}
        except Exception as e:
            logger.error(f"Search error: {e}")
            return {"total": 0, "hits": [], "error": str(e)}

    def get_index_stats(self) -> dict[str, Any]:
        """Get statistics about the index.

        :returns: Dictionary with index statistics
        :rtype: Dict[str, Any]
        """
        try:
            stats = self.client.indices.stats(index=self.index_name)
            count = self.client.count(index=self.index_name)

            return {
                "index_name": self.index_name,
                "document_count": count["count"],
                "size_in_bytes": stats["indices"][self.index_name]["total"]["store"]["size_in_bytes"],
                "health": self.client.cluster.health(index=self.index_name)["status"],
            }
        except Exception as e:
            logger.error(f"Error getting index stats: {e}")
            return {"error": str(e)}

    def health_check(self) -> bool:
        """Check if OpenSearch is healthy and accessible.

        :returns: True if healthy, False otherwise
        :rtype: bool
        """
        try:
            health = self.client.cluster.health()
            return health["status"] in ["green", "yellow"]
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False

    def get_cluster_info(self) -> dict[str, Any] | None:
        """Get OpenSearch cluster information.

        :returns: Dictionary with cluster info or None if error
        :rtype: Optional[Dict[str, Any]]
        """
        try:
            info: dict[str, Any] = self.client.info()
            return info
        except Exception as e:
            logger.error(f"Error getting cluster info: {e}")
            return None

    def get_cluster_health(self) -> dict[str, Any] | None:
        """Get detailed cluster health information.

        :returns: Dictionary with cluster health details or None if error
        :rtype: Optional[Dict[str, Any]]
        """
        try:
            health: dict[str, Any] = self.client.cluster.health()
            return health
        except Exception as e:
            logger.error(f"Error getting cluster health: {e}")
            return None

    def get_index_mapping(self) -> dict[str, Any] | None:
        """Get index mapping (alias for get_mappings for compatibility).

        :returns: Dictionary with index mapping or None if error
        :rtype: Optional[Dict[str, Any]]
        """
        try:
            mappings = self.client.indices.get_mapping(index=self.index_name)
            # Extract just the properties from the nested structure
            if mappings and self.index_name in mappings:
                mapping: dict[str, Any] = mappings[self.index_name].get("mappings", {})
                return mapping
            return {}
        except Exception as e:
            logger.error(f"Error getting index mapping: {e}")
            return None

    def get_index_settings(self) -> dict[str, Any] | None:
        """Get index settings (alias for get_settings for compatibility).

        :returns: Dictionary with index settings or None if error
        :rtype: Optional[Dict[str, Any]]
        """
        try:
            settings = self.client.indices.get_settings(index=self.index_name)
            # Extract just the settings for this index
            if settings and self.index_name in settings:
                index_settings: dict[str, Any] = settings[self.index_name].get("settings", {})
                return index_settings
            return {}
        except Exception as e:
            logger.error(f"Error getting index settings: {e}")
            return None
