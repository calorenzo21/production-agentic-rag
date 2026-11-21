from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from src.models.paper import Paper
from src.schemas.arxiv.paper import PaperBase, PaperCreate


class PaperRepository:
    def __init__(self, session: Session):
        self.session = session

    def create(self, paper: PaperCreate) -> Paper:
        db_paper = Paper(**paper.model_dump())
        self.session.add(db_paper)
        self.session.commit()
        self.session.refresh(db_paper)
        return db_paper

    def get_by_arxiv_id(self, arxiv_id: str) -> Paper | None:
        stmt = select(Paper).where(Paper.arxiv_id == arxiv_id)
        return self.session.scalar(stmt)

    def get_by_id(self, paper_id: UUID) -> Paper | None:
        stmt = select(Paper).where(Paper.id == paper_id)
        return self.session.scalar(stmt)

    def get_all(self, limit: int = 100, offset: int = 0) -> list[Paper]:
        stmt = select(Paper).order_by(Paper.published_date.desc()).limit(limit).offset(offset)
        return list(self.session.scalars(stmt))

    def get_count(self) -> int:
        stmt = select(func.count(Paper.id))
        return self.session.scalar(stmt) or 0

    # The filters use is_() and is_not(). Ruff's fix for `== True` and `!= None` writes `is`, which Python evaluates, not SQL.
    def get_processed_papers(self, limit: int = 100, offset: int = 0) -> list[Paper]:
        """Get papers that have been successfully processed with PDF content."""
        stmt = (
            select(Paper)
            .where(Paper.pdf_processed.is_(True))
            .order_by(Paper.pdf_processing_date.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(stmt))

    def get_unprocessed_papers(self, limit: int = 100, offset: int = 0) -> list[Paper]:
        """Get papers that haven't been processed for PDF content yet."""
        stmt = (
            select(Paper).where(Paper.pdf_processed.is_(False)).order_by(Paper.published_date.desc()).limit(limit).offset(offset)
        )
        return list(self.session.scalars(stmt))

    def get_papers_with_raw_text(self, limit: int = 100, offset: int = 0) -> list[Paper]:
        """Get papers that have raw text content stored."""
        stmt = (
            select(Paper)
            .where(Paper.raw_text.is_not(None))
            .order_by(Paper.pdf_processing_date.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(stmt))

    def get_processing_stats(self) -> dict[str, Any]:
        """Get statistics about PDF processing status."""
        total_papers = self.get_count()

        # Count processed papers
        processed_stmt = select(func.count(Paper.id)).where(Paper.pdf_processed.is_(True))
        processed_papers = self.session.scalar(processed_stmt) or 0

        # Count papers with text
        text_stmt = select(func.count(Paper.id)).where(Paper.raw_text.is_not(None))
        papers_with_text = self.session.scalar(text_stmt) or 0

        return {
            "total_papers": total_papers,
            "processed_papers": processed_papers,
            "papers_with_text": papers_with_text,
            "processing_rate": (processed_papers / total_papers * 100) if total_papers > 0 else 0,
            "text_extraction_rate": (papers_with_text / processed_papers * 100) if processed_papers > 0 else 0,
        }

    def update(self, paper: Paper) -> Paper:
        self.session.add(paper)
        self.session.commit()
        self.session.refresh(paper)
        return paper

    def upsert(self, paper_create: PaperCreate) -> Paper:
        """
        Insert the paper, or update the one with its arxiv_id, in one statement that is safe to run concurrently.

        A Paper without Content, whose PDF failed to parse or was over the limits, updates only the metadata. It never
        removes or overwrites the Content a Paper already has, nor its processed state.
        """
        statement = insert(Paper).values(**paper_create.model_dump())
        updated = paper_create.model_dump(
            exclude_unset=True, include=None if paper_create.pdf_processed else set(PaperBase.model_fields)
        )
        updated_columns = {column: statement.excluded[column] for column in updated if column != "arxiv_id"}
        statement = statement.on_conflict_do_update(
            index_elements=[Paper.arxiv_id],
            # ON CONFLICT skips the model's onupdate, so updated_at is set here.
            set_={**updated_columns, "updated_at": datetime.now(UTC)},
        )
        paper = self.session.scalars(
            statement.returning(Paper),
            # Overwrite a copy of this paper the session may already hold with the row Postgres returns.
            execution_options={"populate_existing": True},
        ).one()
        self.session.commit()
        return paper
