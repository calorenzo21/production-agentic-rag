from functools import lru_cache
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DefaultSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        frozen=True,
        env_nested_delimiter="__",
    )


class ArxivSettings(DefaultSettings):
    """arXiv API client settings."""

    # Without its prefix, it would also read unprefixed variables such as MAX_RESULTS.
    model_config = SettingsConfigDict(env_prefix="ARXIV__")

    base_url: str = "https://export.arxiv.org/api/query"
    namespaces: dict[str, str] = Field(
        default={
            "atom": "http://www.w3.org/2005/Atom",
            "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
            "arxiv": "http://arxiv.org/schemas/atom",
        }
    )
    pdf_cache_dir: str = "./data/arxiv_pdfs"
    rate_limit_delay: float = 3.0  # seconds between requests
    timeout_seconds: int = 30
    max_results: int = 100
    search_category: str = "cs.AI"  # Default category to search


class PDFParserSettings(DefaultSettings):
    """PDF parser service settings."""

    # Without its prefix, it would also read unprefixed variables such as MAX_PAGES.
    model_config = SettingsConfigDict(env_prefix="PDF_PARSER__")

    max_pages: int = 30
    max_file_size_mb: int = 20
    do_ocr: bool = False
    do_table_structure: bool = True


class OpenSearchSettings(DefaultSettings):
    """OpenSearch client settings."""

    # Without its prefix, it would also read unprefixed variables such as HOST and PASSWORD.
    model_config = SettingsConfigDict(env_prefix="OPENSEARCH__")

    host: str = "https://localhost:9200"
    index_name: str = "arxiv-papers"
    max_text_size: int = 1000000
    # The course's OpenSearch needs neither TLS nor a login. This one requires both, as the rag_app user.
    username: str = "rag_app"
    password: str = ""
    ca_certs: str = "secrets/opensearch/ca.crt"


class Settings(DefaultSettings):
    """Application settings."""

    app_version: str = "0.1.0"
    debug: bool = True
    environment: str = "development"
    service_name: str = "rag-api"

    # PostgreSQL configuration
    postgres_database_url: str = "postgresql://rag_user@localhost:5432/rag_db"
    postgres_echo_sql: bool = False
    postgres_pool_size: int = 20
    postgres_max_overflow: int = 0

    # Ollama configuration (used in Week 1 notebook)
    ollama_host: str = "http://localhost:11434"
    # The course's Week 2 types it list[str], which pydantic-settings reads as JSON, so OLLAMA_MODELS=llama3.2:1b fails.
    ollama_models: str | list[str] = Field(default=["llama3.2:1b"])
    ollama_default_model: str = "llama3.2:1b"
    ollama_timeout: int = 300  # 5 minutes for LLM operations

    # arXiv settings
    arxiv: ArxivSettings = Field(default_factory=ArxivSettings)

    # PDF parser settings
    pdf_parser: PDFParserSettings = Field(default_factory=PDFParserSettings)

    # OpenSearch settings
    opensearch: OpenSearchSettings = Field(default_factory=OpenSearchSettings)

    @field_validator("ollama_models", mode="before")
    @classmethod
    def parse_ollama_models(cls, v: Any) -> Any:
        """Parse comma-separated string into list of models."""
        if isinstance(v, str):
            return [model.strip() for model in v.split(",") if model.strip()]
        return v


@lru_cache
def get_settings() -> Settings:
    """Get application settings."""
    return Settings()
