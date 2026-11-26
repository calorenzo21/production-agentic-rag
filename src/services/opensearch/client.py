import logging
from datetime import datetime
from typing import Any

from opensearchpy import OpenSearch, helpers
from opensearchpy.exceptions import NotFoundError, RequestError, TransportError
from src.config import Settings, get_settings
from src.exceptions import OpenSearchException
from src.schemas.api.search import LARGEST_PAGE

from .index_config_hybrid import HYBRID_RRF_PIPELINE, arxiv_papers_chunks_mapping
from .query_builder import PAPERS_MATCHING, QueryBuilder

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
        one, so its count of failed Chunks is always 0 (README.md). It still raises when OpenSearch does not answer. And
        it indexes each Chunk under the ID its entry holds, over any Chunk the Index already holds under it, where the
        course's lets OpenSearch pick a random ID (README.md).

        :param chunks: The Chunks' entries
        :returns: Statistics
        """
        try:
            actions = [{"_index": self.index_name, "_id": chunk["chunk_id"], "_source": chunk} for chunk in chunks]

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

    def delete_paper_chunks(self, arxiv_id: str, keeping: list[str]) -> None:
        """Delete the chunks of a specific paper, but those with the IDs it is keeping.

        Unlike the course's, which deletes all of a Paper's Chunks, it keeps those the Paper's new Chunks were just
        written as, and it raises OpenSearchException when OpenSearch fails to delete the others, as when it does not
        answer, where the course's logs the failure and goes on, leaving them in the Index (README.md).

        :param arxiv_id: ArXiv ID of the paper
        :param keeping: The IDs of the Paper's Chunks to keep
        """
        query = {"bool": {"filter": [{"term": {"arxiv_id": arxiv_id}}], "must_not": [{"ids": {"values": keeping}}]}}
        try:
            response = self.client.delete_by_query(index=self.index_name, body={"query": query}, refresh=True)
        except TransportError as e:
            raise OpenSearchException(str(e)) from e
        logger.info(f"Deleted {response.get('deleted', 0)} chunks for paper {arxiv_id}")

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
        one_per_paper: bool = False,
    ) -> dict[str, Any]:
        """Unified search method supporting BM25 and hybrid modes.

        Unlike the course's, it raises OpenSearchException when OpenSearch fails the Search, as when it does not answer,
        the Index does not exist or the RRF pipeline is missing, rather than answering as if no Chunk matched (README.md).
        And on request, it returns only the best Chunk of each Paper, and counts the Papers, as the endpoint's Search does.
        Without it, it returns every Chunk that matches, as the course's does for Week 5's /ask (README.md).

        :param query: Text query for search
        :param query_embedding: Optional embedding for hybrid search
        :param size: Number of results to return
        :param from_: Offset for pagination
        :param categories: Optional category filter
        :param latest: Sort by date instead of relevance
        :param use_hybrid: If True and embedding provided, use hybrid search
        :param min_score: Minimum score threshold
        :param one_per_paper: Return only the best Chunk of each Paper, and count the Papers rather than the Chunks
        :returns: Search results
        """
        try:
            # If no embedding provided or hybrid disabled, use BM25 only
            if not query_embedding or not use_hybrid:
                return self._search_bm25_only(
                    query=query,
                    size=size,
                    from_=from_,
                    categories=categories,
                    latest=latest,
                    min_score=min_score,
                    one_per_paper=one_per_paper,
                )

            # Use native OpenSearch hybrid search with RRF pipeline
            return self._search_hybrid_native(
                query=query,
                query_embedding=query_embedding,
                size=size,
                from_=from_,
                categories=categories,
                latest=latest,
                min_score=min_score,
                one_per_paper=one_per_paper,
            )
        except TransportError as e:
            raise OpenSearchException(f"OpenSearch failed the Search: {e}") from e

    def _search_bm25_only(
        self,
        query: str,
        size: int,
        from_: int,
        categories: list[str] | None,
        latest: bool,
        min_score: float,
        one_per_paper: bool,
    ) -> dict[str, Any]:
        """Pure BM25 search implementation. Unlike the course's, it leaves out the Chunks that score below the minimum
        score, which the course's ignores (README.md)."""
        builder = QueryBuilder(
            query=query,
            size=size,
            from_=from_,
            categories=categories,
            latest_papers=latest,
            one_per_paper=one_per_paper,
            min_score=min_score,
        )
        search_body = builder.build()

        response = self.client.search(index=self.index_name, body=search_body)

        # The hits' total counts the Chunks that match.
        total = response["aggregations"][PAPERS_MATCHING]["value"] if one_per_paper else response["hits"]["total"]["value"]
        results: dict[str, Any] = {"total": total, "hits": []}

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
        self,
        query: str,
        query_embedding: list[float],
        size: int,
        from_: int,
        categories: list[str] | None,
        latest: bool,
        min_score: float,
        one_per_paper: bool,
    ) -> dict[str, Any]:
        """Native OpenSearch hybrid search with RRF pipeline.

        Unlike the course's, it filters both halves by Category, as OpenSearch 2.19's hybrid query has no filter they
        share: the k-NN half inside its search, which faiss allows. The course's filters only the BM25 half, so its k-NN
        half returns Chunks of any Category (README.md).

        And unlike the course's, which ignores the offset and the newest-first order, it fetches the same window of fused
        Chunks for every page, then orders and pages them itself, so that every page is cut from the same Chunks, or Papers
        (README.md). OpenSearch cannot page over Papers: 2.19 ignores collapse in a hybrid query, and its collapse response
        processor removes a Paper's other Chunks only from the page it fetched. Nor can it order a hybrid query by
        publication date, then by score.
        """
        # How many Chunks each half keeps, the same on every page, as OpenSearch 2.19 needs to page a hybrid query. A
        # Search by words and meaning reaches only the Papers of their Chunks.
        pagination_depth = self.settings.opensearch.hybrid_search_size_multiplier * LARGEST_PAGE

        # Without the minimum score, which applies to the fused scores below, so that its query stays a bool query, whose
        # filter the k-NN half takes.
        builder = QueryBuilder(query=query, size=pagination_depth, from_=0, categories=categories, latest_papers=False)
        bm25_search_body = builder.build()

        bm25_query = bm25_search_body["query"]

        knn_query: dict[str, Any] = {"vector": query_embedding, "k": pagination_depth}
        # The BM25 half's own filter, so that both halves filter alike.
        bm25_filter = bm25_query["bool"].get("filter")
        if bm25_filter:
            knn_query["filter"] = {"bool": {"filter": bm25_filter}}

        hybrid_query = {
            "hybrid": {"queries": [bm25_query, {"knn": {"embedding": knn_query}}], "pagination_depth": pagination_depth}
        }

        search_body = {
            # The whole window: every Chunk either half keeps, which is twice as many when the halves share none.
            "size": 2 * pagination_depth,
            "query": hybrid_query,
            "_source": bm25_search_body["_source"],
            "highlight": bm25_search_body["highlight"],
        }

        # Execute search with RRF pipeline
        response = self.client.search(
            index=self.index_name, body=search_body, params={"search_pipeline": self.settings.opensearch.rrf_pipeline_name}
        )

        chunks: list[dict[str, Any]] = []

        for hit in response["hits"]["hits"]:
            # To the fused score, which a search's min_score is not: OpenSearch 2.19 applies it to each half's scores.
            if hit["_score"] < min_score:
                continue

            chunk = hit["_source"]
            chunk["score"] = hit["_score"]
            chunk["chunk_id"] = hit["_id"]

            if "highlight" in hit:
                chunk["highlights"] = hit["highlight"]

            chunks.append(chunk)

        if one_per_paper:
            # Ranked by their fused scores, so each Paper's first Chunk is its best.
            best_chunks: dict[str, dict[str, Any]] = {}
            for chunk in chunks:
                best_chunks.setdefault(chunk["arxiv_id"], chunk)
            chunks = list(best_chunks.values())

        if latest:
            # A stable sort, so that those published at the same time stay ranked by their fused scores.
            chunks.sort(key=lambda chunk: datetime.fromisoformat(chunk["published_date"]), reverse=True)

        results: dict[str, Any] = {"total": len(chunks), "hits": chunks[from_ : from_ + size]}
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
