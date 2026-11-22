import re
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# An arXiv ID can end with a Version, such as v2. The rest identifies the Paper, in either of arXiv's ID formats.
VERSIONED_ARXIV_ID = re.compile(r"(?P<paper>.+?)v(?P<version>\d+)")


def split_version(arxiv_id: str) -> tuple[str, int | None]:
    """A Paper's arXiv ID without its Version, and the Version, None for an ID without one."""
    match = VERSIONED_ARXIV_ID.fullmatch(arxiv_id)
    if match is None:
        return arxiv_id, None
    return match["paper"], int(match["version"])


class ArxivPaper(BaseModel):
    """Schema for arXiv API response data."""

    # With its Version, as arXiv lists it, so that the PDF downloaded is that Version's.
    arxiv_id: str = Field(..., description="arXiv paper ID")
    title: str = Field(..., description="Paper title")
    authors: list[str] = Field(..., description="List of author names")
    abstract: str = Field(..., description="Paper abstract")
    categories: list[str] = Field(..., description="Paper categories")
    published_date: str = Field(..., description="Date published on arXiv (ISO format)")
    pdf_url: str = Field(..., description="URL to PDF")


class PaperBase(BaseModel):
    # Core arXiv metadata
    arxiv_id: str = Field(..., description="arXiv paper ID, without its Version, such as 2411.00001")
    version: int = Field(..., description="The Version the curator holds, such as 2 for v2")
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


class PaperListItem(PaperBase):
    """A Paper as the list returns it: its metadata, without its Content, which can be many times larger."""

    id: UUID

    # PDF processing metadata
    parser_used: str | None = Field(default=None, description="Which parser was used")
    parser_metadata: dict[str, Any] | None = Field(default=None, description="Additional parser metadata")
    pdf_processed: bool = Field(default=False, description="Whether PDF was successfully processed")
    pdf_processing_date: datetime | None = Field(default=None, description="When PDF was processed")

    # Timestamps
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PaperResponse(PaperListItem):
    """Schema for paper API responses with all content."""

    # Parsed PDF content (optional fields)
    raw_text: str | None = Field(default=None, description="Full raw text extracted from PDF")
    sections: list[dict[str, Any]] | None = Field(default=None, description="List of sections with titles and content")
    references: list[dict[str, Any]] | None = Field(default=None, description="List of references if extracted")


class PaperSearchResponse(BaseModel):
    papers: list[PaperListItem]
    total: int
