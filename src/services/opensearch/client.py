import logging
from typing import Any

from opensearchpy import OpenSearch, helpers
from opensearchpy.exceptions import NotFoundError, RequestError, TransportError
from src.config import Settings, get_settings
from src.exceptions import OpenSearchException

from .index_config_hybrid import HYBRID_RRF_PIPELINE, arxiv_papers_chunks_mapping
from .query_builder import QueryBuilder

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
        """Setup the hybrid search index and RRF pipeline."""
        results = {}
        results["hybrid_index"] = self._create_hybrid_index(force)
        results["rrf_pipeline"] = self._create_rrf_pipeline(force)
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

    def _create_rrf_pipeline(self, force: bool = False) -> bool:
        """Create RRF search pipeline for native hybrid search, named by OPENSEARCH__RRF_PIPELINE_NAME.

        Unlike the course's, it looks the pipeline up, and deletes it on force, through the search pipeline API rather than
        the ingest pipeline API, where it is never found, so the course's always creates it again (README.md). On force it
        deletes it as the course means to, although creating it again would replace it, so a failure to create it then
        leaves no pipeline. And like the creation of the Index, it reports a failure by returning False rather than raising
        it (README.md).

        :param force: If True, recreate pipeline even if it exists
        :returns: True if created, False if it already exists or could not be created
        """
        pipeline_id = self.settings.opensearch.rrf_pipeline_name
        try:
            if self._search_pipeline_exists(pipeline_id):
                if not force:
                    logger.info(f"RRF pipeline already exists: {pipeline_id}")
                    return False
                self.client.search_pipeline.delete(id=pipeline_id)
                logger.info(f"Deleted existing RRF pipeline: {pipeline_id}")

            # Creating it again, as the API's 4 workers may at startup, replaces it without an error.
            self.client.search_pipeline.put(id=pipeline_id, body=HYBRID_RRF_PIPELINE)

            logger.info(f"Created RRF search pipeline: {pipeline_id}")
            return True

        except Exception as e:
            logger.error(f"Error creating RRF pipeline: {e}")
            return False

    def _search_pipeline_exists(self, pipeline_id: str) -> bool:
        """Whether OpenSearch holds the search pipeline with this ID."""
        try:
            self.client.search_pipeline.get(id=pipeline_id)
        except NotFoundError:
            return False
        return True

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

    def delete_week3_index(self) -> None:
        """
        Delete the Week 3 index, which held each Paper whole, and is named the index name alone, if it is still there. The
        chunk index replaces it, and the course leaves it behind, as it starts Week 4 from empty volumes (README.md).
        Raises OpenSearchException when OpenSearch fails to delete it, as when it does not answer.
        """
        week3_index = self.settings.opensearch.index_name
        try:
            if not self.client.indices.exists(index=week3_index):
                return
            self.client.indices.delete(index=week3_index)
        except TransportError as e:
            raise OpenSearchException(str(e)) from e
        logger.info(f"Deleted the Week 3 index {week3_index}, which {self.index_name} replaces")

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

    def search_unified(
        self,
        query: str,
        query_embedding: list[float] | None = None,
        size: int = 10,
        from_: int = 0,
        categories: list[str] | None = None,
        latest: bool = False,
        use_hybrid: bool = True,
        min_score: float = 0.0,
    ) -> dict[str, Any]:
        """Unified search method supporting BM25 and hybrid modes.

        Unlike the course's, it raises OpenSearchException when OpenSearch fails the Search, as when it does not answer,
        the Index does not exist or the RRF pipeline is missing, rather than answering as if no Chunk matched (README.md).

        :param query: Text query for search
        :param query_embedding: Optional embedding for hybrid search
        :param size: Number of results to return
        :param from_: Offset for pagination
        :param categories: Optional category filter
        :param latest: Sort by date instead of relevance
        :param use_hybrid: If True and embedding provided, use hybrid search
        :param min_score: Minimum score threshold
        :returns: Search results
        """
        try:
            # If no embedding provided or hybrid disabled, use BM25 only
            if not query_embedding or not use_hybrid:
                return self._search_bm25_only(query=query, size=size, from_=from_, categories=categories, latest=latest)

            # Use native OpenSearch hybrid search with RRF pipeline
            return self._search_hybrid_native(
                query=query, query_embedding=query_embedding, size=size, categories=categories, min_score=min_score
            )
        except TransportError as e:
            raise OpenSearchException(f"OpenSearch failed the Search: {e}") from e

    def _search_bm25_only(self, query: str, size: int, from_: int, categories: list[str] | None, latest: bool) -> dict[str, Any]:
        """Pure BM25 search implementation."""
        builder = QueryBuilder(query=query, size=size, from_=from_, categories=categories, latest_papers=latest)
        search_body = builder.build()

        response = self.client.search(index=self.index_name, body=search_body)

        results: dict[str, Any] = {"total": response["hits"]["total"]["value"], "hits": []}

        for hit in response["hits"]["hits"]:
            chunk = hit["_source"]
            chunk["score"] = hit["_score"]
            chunk["chunk_id"] = hit["_id"]

            if "highlight" in hit:
                chunk["highlights"] = hit["highlight"]

            results["hits"].append(chunk)

        logger.info(f"BM25 search for '{query[:50]}...' returned {results['total']} results")
        return results

    def _search_hybrid_native(
        self, query: str, query_embedding: list[float], size: int, categories: list[str] | None, min_score: float
    ) -> dict[str, Any]:
        """Native OpenSearch hybrid search with RRF pipeline.

        Unlike the course's, it filters both halves by Category, as OpenSearch 2.19's hybrid query has no filter they
        share: the k-NN half inside its search, which faiss allows. The course's filters only the BM25 half, so its k-NN
        half returns Chunks of any Category (README.md).
        """
        builder = QueryBuilder(query=query, size=size * 2, from_=0, categories=categories, latest_papers=False)
        bm25_search_body = builder.build()

        bm25_query = bm25_search_body["query"]

        knn_query: dict[str, Any] = {"vector": query_embedding, "k": size * 2}
        # The BM25 half's own filter, so that both halves filter alike.
        bm25_filter = bm25_query["bool"].get("filter")
        if bm25_filter:
            knn_query["filter"] = {"bool": {"filter": bm25_filter}}

        hybrid_query = {"hybrid": {"queries": [bm25_query, {"knn": {"embedding": knn_query}}]}}

        search_body = {
            "size": size,
            "query": hybrid_query,
            "_source": bm25_search_body["_source"],
            "highlight": bm25_search_body["highlight"],
        }

        # Execute search with RRF pipeline
        response = self.client.search(
            index=self.index_name, body=search_body, params={"search_pipeline": self.settings.opensearch.rrf_pipeline_name}
        )

        results: dict[str, Any] = {"total": response["hits"]["total"]["value"], "hits": []}

        for hit in response["hits"]["hits"]:
            if hit["_score"] < min_score:
                continue

            chunk = hit["_source"]
            chunk["score"] = hit["_score"]
            chunk["chunk_id"] = hit["_id"]

            if "highlight" in hit:
                chunk["highlights"] = hit["highlight"]

            results["hits"].append(chunk)

        results["total"] = len(results["hits"])
        logger.info(f"Native hybrid search for '{query[:50]}...' returned {results['total']} results")
        return results

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
