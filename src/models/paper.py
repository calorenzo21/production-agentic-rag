import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, Column, DateTime, Integer, String, Text, false
from sqlalchemy.dialects.postgresql import UUID
from src.models.base import Base


class Paper(Base):
    __tablename__ = "papers"

    # Core arXiv metadata
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Without its Version, which `version` holds, so that every Version of a Paper is the same Paper (ADR 0004).
    arxiv_id = Column(String, unique=True, nullable=False, index=True)
    version = Column(Integer, nullable=False)
    title = Column(String, nullable=False)
    authors = Column(JSON, nullable=False)  # List of author names
    abstract = Column(Text, nullable=False)
    categories = Column(JSON, nullable=False)  # List of category tags
    published_date = Column(DateTime, nullable=False)
    pdf_url = Column(String, nullable=False)

    # Parsed PDF content (added for comprehensive storage)
    raw_text = Column(Text, nullable=True)
    sections = Column(JSON, nullable=True)
    # A reserved word in PostgreSQL, which SQLAlchemy quotes wherever it writes the column.
    references = Column(JSON, nullable=True)

    # PDF processing metadata
    parser_used = Column(String, nullable=True)
    parser_metadata = Column(JSON, nullable=True)
    # The server default makes the Papers held before the column existed count as not processed.
    pdf_processed = Column(Boolean, default=False, server_default=false(), nullable=False)
    pdf_processing_date = Column(DateTime, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))
    updated_at = Column(DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))
