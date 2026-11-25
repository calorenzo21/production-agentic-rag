from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).parent.parent
ENV_FILE_PATH = PROJECT_ROOT / ".env"


class DefaultSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=[".env", str(ENV_FILE_PATH)],
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
    # The course's Week 3 creates it whenever the settings load. The arXiv client creates it when it first needs it
    # instead, as the API and the migrations load the settings too, as a user who cannot create it (README.md).
    pdf_cache_dir: str = "./data/arxiv_pdfs"
    rate_limit_delay: float = 3.0  # seconds between requests
    timeout_seconds: int = 30
    # The course's Week 3 lowers it to 15. README.md records keeping 100 as a Deviation.
    max_results: int = 100
    search_category: str = "cs.AI"  # Default category to search
    download_max_retries: int = 3  # attempts at each PDF download, the first one included
    download_retry_delay_base: float = 5.0  # the nth retry waits n times this many seconds
    max_concurrent_downloads: int = 5
    max_concurrent_parsing: int = 1


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
    environment: Literal["development", "staging", "production"] = "development"
    service_name: str = "rag-api"

    # PostgreSQL configuration
    postgres_database_url: str = "postgresql://rag_user@localhost:5432/rag_db"
    postgres_echo_sql: bool = False
    postgres_pool_size: int = 20
    postgres_max_overflow: int = 0

    # Ollama configuration (used in Week 1 notebook)
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "llama3.2:1b"
    ollama_timeout: int = 300  # 5 minutes for LLM operations

    # Jina AI embeddings configuration. make setup cannot generate the key: the maintainer pastes it into .env.
    jina_api_key: str = ""

    # arXiv settings
    arxiv: ArxivSettings = Field(default_factory=ArxivSettings)

    # PDF parser settings
    pdf_parser: PDFParserSettings = Field(default_factory=PDFParserSettings)

    # OpenSearch settings
    opensearch: OpenSearchSettings = Field(default_factory=OpenSearchSettings)

    @field_validator("postgres_database_url")
    @classmethod
    def validate_database_url(cls, v: str) -> str:
        if not v.startswith(("postgresql://", "postgresql+psycopg2://")):
            raise ValueError("Database URL must start with 'postgresql://' or 'postgresql+psycopg2://'")
        return v


@lru_cache
def get_settings() -> Settings:
    """Get application settings."""
    return Settings()
