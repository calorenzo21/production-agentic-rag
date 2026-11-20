from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PaperBase(BaseModel):
    arxiv_id: str = Field(..., description="arXiv paper ID")
    title: str = Field(..., description="Paper title")
    authors: list[str] = Field(..., description="List of author names")
    abstract: str = Field(..., description="Paper abstract")
    categories: list[str] = Field(..., description="Paper categories")
    published_date: datetime = Field(..., description="Date published on arXiv")
    pdf_url: str = Field(..., description="URL to PDF")


class PaperCreate(PaperBase):
    """Schema for creating a paper with optional parsed content."""

    # Parsed PDF content (optional - added when PDF is processed)
    raw_text: str | None = Field(default=None, description="Full raw text extracted from PDF")
    sections: list[dict[str, Any]] | None = Field(default=None, description="List of sections with titles and content")
    references: list[dict[str, Any]] | None = Field(default=None, description="List of references if extracted")

    # PDF processing metadata (optional)
    parser_used: str | None = Field(default=None, description="Which parser was used (DOCLING, GROBID, etc.)")
    parser_metadata: dict[str, Any] | None = Field(default=None, description="Additional parser metadata")
    pdf_processed: bool | None = Field(default=False, description="Whether PDF was successfully processed")
    pdf_processing_date: datetime | None = Field(default=None, description="When PDF was processed")


class PaperResponse(PaperBase):
    id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PaperSearchResponse(BaseModel):
    papers: list[PaperResponse]
    total: int
