from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from src.models.paper import Paper
from src.schemas.paper import PaperCreate


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
        return self.session.query(Paper).filter(Paper.arxiv_id == arxiv_id).first()

    def get_by_id(self, paper_id: UUID) -> Paper | None:
        return self.session.query(Paper).filter(Paper.id == paper_id).first()

    def get_all(self, limit: int = 100, offset: int = 0) -> list[Paper]:
        return self.session.query(Paper).order_by(Paper.published_date.desc()).limit(limit).offset(offset).all()

    def update(self, paper: Paper) -> Paper:
        self.session.add(paper)
        self.session.commit()
        self.session.refresh(paper)
        return paper

    def upsert(self, paper_create: PaperCreate) -> Paper:
        """Insert the paper, or update the one with its arxiv_id, in one statement that is safe to run concurrently."""
        statement = insert(Paper).values(**paper_create.model_dump())
        updated_columns = {
            column: statement.excluded[column] for column in paper_create.model_dump(exclude_unset=True) if column != "arxiv_id"
        }
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
