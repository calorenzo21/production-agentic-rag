import logging
from typing import Any

logger = logging.getLogger(__name__)

# The aggregation that counts the Papers a Search by words alone matches, when it returns one Chunk per Paper.
PAPERS_MATCHING = "papers_matching"


class QueryBuilder:
    """
    Query builder for the BM25 Search of the Chunks the Index holds.

    Builds complex OpenSearch queries with proper scoring, filtering, and highlighting. The course's also builds Week 3's
    query of whole Papers, for the index Week 4 replaces, which nothing calls (README.md).
    """

    def __init__(
        self,
        query: str,
        size: int = 10,
        from_: int = 0,
        fields: list[str] | None = None,
        categories: list[str] | None = None,
        track_total_hits: bool = True,
        latest_papers: bool = False,
        one_per_paper: bool = False,
        min_score: float = 0.0,
    ):
        """Initialize query builder.

        :param query: Search query text
        :param size: Number of results to return
        :param from_: Offset for pagination
        :param fields: Fields to search in (if None, the Chunk's text, the title, the abstract and the authors)
        :param categories: Filter by categories
        :param track_total_hits: Whether to track total hits accurately
        :param latest_papers: Sort by publication date instead of relevance
        :param one_per_paper: Return only the best Chunk of each Paper, and count the Papers, which the course does not
            (README.md)
        :param min_score: Leave out the Chunks whose BM25 score is below it, which the course does not (README.md)
        """
        self.query = query
        self.size = size
        self.from_ = from_
        self.categories = categories
        self.track_total_hits = track_total_hits
        self.latest_papers = latest_papers
        self.one_per_paper = one_per_paper
        self.min_score = min_score

        if fields is None:
            # The course's leaves out the authors, which Week 3's searched, with this weight (README.md).
            self.fields = ["chunk_text^3", "title^2", "abstract^1", "authors^1"]
        else:
            self.fields = fields

    def build(self) -> dict[str, Any]:
        """Build the complete OpenSearch query.

        :returns: Complete query dictionary ready for OpenSearch
        """
        query_body = {
            "query": self._build_query(),
            "size": self.size,
            "from": self.from_,
            "track_total_hits": self.track_total_hits,
            "_source": self._build_source_fields(),
            "highlight": self._build_highlight(),
        }

        sort = self._build_sort()
        if sort:
            query_body["sort"] = sort

        if self.one_per_paper:
            # Of each Paper's Chunks, the first in the order sorted, so the best, as all of a Paper's Chunks share its
            # publication date. OpenSearch pages over them, and counts the distinct arXiv IDs, as the hits' total counts
            # Chunks. Above its precision threshold, 3,000 by default, it counts them only approximately, so the count
            # would drift as the Papers held grow: 40,000 is the highest threshold it takes.
            query_body["collapse"] = {"field": "arxiv_id"}
            query_body["aggs"] = {PAPERS_MATCHING: {"cardinality": {"field": "arxiv_id", "precision_threshold": 40_000}}}

        return query_body

    def _build_query(self) -> dict[str, Any]:
        """Build the main query with filters.

        :returns: Query dictionary with bool structure
        """
        must_clauses = []

        if self.query.strip():
            must_clauses.append(self._build_text_query())

        filter_clauses = self._build_filters()

        bool_query = {}

        if must_clauses:
            bool_query["must"] = must_clauses
        else:
            bool_query["must"] = [{"match_all": {}}]

        if filter_clauses:
            bool_query["filter"] = filter_clauses

        if self.min_score:
            # Its own min_score leaves the Chunks below it out of the query, and so out of the count of Papers too. A
            # search's min_score leaves them out of the hits only, in OpenSearch 2.19, so the count would still hold them.
            return {"function_score": {"query": {"bool": bool_query}, "min_score": self.min_score}}

        return {"bool": bool_query}

    def _build_text_query(self) -> dict[str, Any]:
        """Build the main text search query.

        :returns: Multi-match query for text search
        """
        return {
            "multi_match": {
                "query": self.query,
                "fields": self.fields,
                "type": "best_fields",
                "operator": "or",
                "fuzziness": "AUTO",
                "prefix_length": 2,
            }
        }

    def _build_filters(self) -> list[dict[str, Any]]:
        """Build filter clauses for the query.

        :returns: List of filter clauses
        """
        filters = []

        if self.categories:
            filters.append({"terms": {"categories": self.categories}})

        return filters

    def _build_source_fields(self) -> dict[str, list[str]]:
        """Define which fields to return in results: all of a Chunk's but its embedding.

        :returns: Source field configuration
        """
        return {"excludes": ["embedding"]}

    def _build_highlight(self) -> dict[str, Any]:
        """Build highlighting configuration.

        :returns: Highlight configuration dictionary
        """
        return {
            "fields": {
                "chunk_text": {
                    "fragment_size": 150,
                    "number_of_fragments": 2,
                    "pre_tags": ["<mark>"],
                    "post_tags": ["</mark>"],
                },
                "title": {"fragment_size": 0, "number_of_fragments": 0, "pre_tags": ["<mark>"], "post_tags": ["</mark>"]},
                "abstract": {
                    "fragment_size": 150,
                    "number_of_fragments": 1,
                    "pre_tags": ["<mark>"],
                    "post_tags": ["</mark>"],
                },
            },
            "require_field_match": False,
        }

    def _build_sort(self) -> list[dict[str, Any] | str] | None:
        """Build sorting configuration.

        :returns: Sort configuration or None for relevance scoring
        """
        if self.latest_papers:
            return [{"published_date": {"order": "desc"}}, "_score"]

        if self.query.strip():
            return None

        return [{"published_date": {"order": "desc"}}, "_score"]
