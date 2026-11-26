"""OpenSearch index configuration for hybrid search (BM25 + Vector).

This configuration supports both keyword search (BM25) and vector similarity search
using HNSW algorithm for approximate nearest neighbor search.
"""

from typing import Any


def arxiv_papers_chunks_mapping(vector_dimension: int, vector_space_type: str) -> dict[str, Any]:
    """
    Index mapping for chunked papers with vector embeddings, of the dimension and space type the settings give. The
    course's is a constant, which hard-codes them as 1024 and cosinesimil (README.md).
    """
    return {
        "settings": {
            "number_of_shards": 1,
            "number_of_replicas": 0,
            "index.knn": True,
            "index.knn.space_type": vector_space_type,
            "analysis": {
                "analyzer": {
                    "standard_analyzer": {"type": "standard", "stopwords": "_english_"},
                    "text_analyzer": {"type": "custom", "tokenizer": "standard", "filter": ["lowercase", "stop", "snowball"]},
                }
            },
        },
        "mappings": {
            "dynamic": "strict",
            "properties": {
                "chunk_id": {"type": "keyword"},
                "arxiv_id": {"type": "keyword"},
                "paper_id": {"type": "keyword"},
                "chunk_index": {"type": "integer"},
                "chunk_text": {
                    "type": "text",
                    "analyzer": "text_analyzer",
                    "fields": {"keyword": {"type": "keyword", "ignore_above": 256}},
                },
                "chunk_word_count": {"type": "integer"},
                "start_char": {"type": "integer"},
                "end_char": {"type": "integer"},
                "embedding": {
                    "type": "knn_vector",
                    "dimension": vector_dimension,
                    "method": {
                        "name": "hnsw",  # Hierarchical Navigable Small World
                        "space_type": vector_space_type,
                        # The course's nmslib cannot filter inside a k-NN search, and 2.19 deprecates it (README.md).
                        "engine": "faiss",
                        "parameters": {
                            "ef_construction": 512,  # Higher value = better recall, slower indexing
                            "m": 16,  # Number of bi-directional links
                        },
                    },
                },
                "title": {
                    "type": "text",
                    "analyzer": "text_analyzer",
                    "fields": {"keyword": {"type": "keyword", "ignore_above": 256}},
                },
                "authors": {
                    "type": "text",
                    "analyzer": "standard_analyzer",
                    "fields": {"keyword": {"type": "keyword", "ignore_above": 256}},
                },
                "abstract": {"type": "text", "analyzer": "text_analyzer"},
                "categories": {"type": "keyword"},
                "published_date": {"type": "date"},
                # The course's chunk index has no PDF link, so a Search cannot return the Paper's (README.md).
                "pdf_url": {"type": "keyword"},
                "section_title": {"type": "keyword"},
                "embedding_model": {"type": "keyword"},
                "created_at": {"type": "date"},
                "updated_at": {"type": "date"},
            },
        },
    }


# The search pipeline that fuses a hybrid query's halves, without its ID, which OPENSEARCH__RRF_PIPELINE_NAME gives. The
# course's also holds its ID, so its setting is never read (README.md).
HYBRID_RRF_PIPELINE: dict[str, Any] = {
    "description": "Post processor for hybrid RRF search",
    "phase_results_processors": [
        {
            "score-ranker-processor": {
                "combination": {
                    "technique": "rrf",  # Reciprocal Rank Fusion
                    # Default k=60 for RRF formula: 1/(k+rank). OpenSearch 2.19 reads it from the combination's parameters,
                    # so it ignores this one, and applies its default, the same 60.
                    "rank_constant": 60,
                }
            }
        }
    ],
}
