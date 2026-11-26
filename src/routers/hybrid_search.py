import logging

from fastapi import APIRouter, HTTPException
from src.dependencies import EmbeddingsDep, OpenSearchDep
from src.exceptions import OpenSearchException
from src.schemas.api.search import HybridSearchRequest, SearchHit, SearchResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/hybrid-search", tags=["hybrid-search"])


@router.post("/", response_model=SearchResponse)
async def hybrid_search(
    request: HybridSearchRequest, opensearch_client: OpenSearchDep, embeddings_service: EmbeddingsDep
) -> SearchResponse:
    """
    Hybrid search endpoint supporting multiple search modes.
    """
    # Unlike the course's, it checks no cluster health before each Search: a Search that OpenSearch fails tells instead,
    # and answers 503, where the course's answers 200 with no hits. Neither the 503 nor a 500 holds the exception's
    # text, which the course's 500 does (README.md).
    try:
        query_embedding = None
        if request.use_hybrid:
            # One attempt, so that the Search falls back to words alone at Jina's first failure, a rate limit included,
            # rather than waiting through the retries of the indexing's embeddings (README.md).
            try:
                query_embedding = await embeddings_service.embed_query(request.query)
                logger.info("Generated query embedding for hybrid search")
            except Exception as e:
                logger.warning(f"Failed to generate embeddings, falling back to BM25: {e}")
                query_embedding = None

        logger.info(f"Hybrid search: '{request.query}' (hybrid: {request.use_hybrid and query_embedding is not None})")

        results = opensearch_client.search_unified(
            query=request.query,
            query_embedding=query_embedding,
            size=request.size,
            from_=request.from_,
            categories=request.categories,
            latest=request.latest_papers,
            use_hybrid=request.use_hybrid,
            min_score=request.min_score,
        )

        hits = []
        for hit in results.get("hits", []):
            hits.append(
                SearchHit(
                    arxiv_id=hit.get("arxiv_id", ""),
                    title=hit.get("title", ""),
                    authors=hit.get("authors"),
                    abstract=hit.get("abstract"),
                    published_date=hit.get("published_date"),
                    pdf_url=hit.get("pdf_url"),
                    score=hit.get("score", 0.0),
                    highlights=hit.get("highlights"),
                    chunk_text=hit.get("chunk_text"),
                    chunk_id=hit.get("chunk_id"),
                    # The Index holds it as section_title. The course's reads section_name, so always null (README.md).
                    section_name=hit.get("section_title"),
                )
            )

        search_response = SearchResponse(
            query=request.query,
            total=results.get("total", 0),
            hits=hits,
            size=request.size,
            **{"from": request.from_},
            search_mode="hybrid" if (request.use_hybrid and query_embedding) else "bm25",
        )

        logger.info(f"Search completed: {search_response.total} results returned")
        return search_response

    except OpenSearchException as e:
        logger.error(f"Hybrid search error: {e}")
        raise HTTPException(status_code=503, detail="Search service is currently unavailable") from e
    except Exception as e:
        # With its traceback, as the caller no longer gets the exception's text.
        logger.exception("Hybrid search error")
        raise HTTPException(status_code=500, detail="Search failed") from e
