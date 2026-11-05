"""Create the papers table.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "papers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("arxiv_id", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("authors", sa.JSON(), nullable=False),
        sa.Column("abstract", sa.Text(), nullable=False),
        sa.Column("categories", sa.JSON(), nullable=False),
        sa.Column("published_date", sa.DateTime(), nullable=False),
        sa.Column("pdf_url", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index(op.f("ix_papers_arxiv_id"), "papers", ["arxiv_id"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_papers_arxiv_id"), table_name="papers")
    op.drop_table("papers")
