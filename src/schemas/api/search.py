from pydantic import BaseModel, ConfigDict, Field

# The most results a page of a Search holds.
LARGEST_PAGE = 100


class HybridSearchRequest(BaseModel):
    """Request model for hybrid search supporting all search modes."""

    query: str = Field(..., description="Search query text", min_length=1, max_length=500)
    size: int = Field(10, description="Number of results to return", ge=1, le=LARGEST_PAGE)
    from_: int = Field(0, description="Offset for pagination", ge=0, alias="from")
    categories: list[str] | None = Field(None, description="Filter by arXiv categories (e.g., ['cs.AI', 'cs.LG'])")
    latest_papers: bool = Field(False, description="Sort by publication date instead of relevance")
    use_hybrid: bool = Field(True, description="Enable hybrid search (BM25 + vector) with automatic embedding generation")
    min_score: float = Field(0.0, description="Minimum score threshold for results", ge=0.0)

    model_config = ConfigDict(
        validate_by_name=True,
        json_schema_extra={
            "example": {
                "query": "machine learning neural networks",
                "size": 10,
                "categories": ["cs.AI", "cs.LG"],
                "latest_papers": False,
                "use_hybrid": True,
            }
        },
    )


class SearchHit(BaseModel):
    """Individual search result."""

    arxiv_id: str
    title: str
    # A list, as /api/v1/papers/ returns them. The course's is one comma-separated string (README.md).
    authors: list[str]
    abstract: str | None
    published_date: str | None
    pdf_url: str | None
    score: float
    highlights: dict[str, list[str]] | None = None

    # Chunk-specific fields (for unified search)
    chunk_text: str | None = Field(None, description="Text content of the matching chunk")
    chunk_id: str | None = Field(None, description="Unique identifier of the chunk")
    section_name: str | None = Field(None, description="Section name where the chunk was found")


class SearchResponse(BaseModel):
    """Search response model. Unlike the course's, it has no error field, which nothing sets: a Search that OpenSearch fails
    answers 503 instead (README.md)."""

    query: str
    total: int
    hits: list[SearchHit]
    size: int = Field(description="Number of results requested")
    from_: int = Field(alias="from", description="Offset used for pagination")
    search_mode: str | None = Field(None, description="Search mode used: bm25, vector, or hybrid")

    model_config = ConfigDict(validate_by_name=True)
