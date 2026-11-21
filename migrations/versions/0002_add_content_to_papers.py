"""Add the Content columns and the processing metadata to the papers table.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

COLUMNS = (
    sa.Column("raw_text", sa.Text(), nullable=True),
    sa.Column("sections", sa.JSON(), nullable=True),
    # A reserved word in PostgreSQL, which SQLAlchemy quotes wherever it writes the column.
    sa.Column("references", sa.JSON(), nullable=True),
    sa.Column("parser_used", sa.String(), nullable=True),
    sa.Column("parser_metadata", sa.JSON(), nullable=True),
    # The server default makes the Papers already held count as not processed.
    sa.Column("pdf_processed", sa.Boolean(), server_default=sa.false(), nullable=False),
    sa.Column("pdf_processing_date", sa.DateTime(), nullable=True),
)


def upgrade() -> None:
    for column in COLUMNS:
        op.add_column("papers", column)


def downgrade() -> None:
    for column in reversed(COLUMNS):
        op.drop_column("papers", column.name)
