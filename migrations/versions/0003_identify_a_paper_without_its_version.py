"""Identify a Paper by its arXiv ID without its Version, which a column of its own holds (ADR 0004).

The Papers held under several Versions become one, at the latest Version, which takes the Content of the latest
Version that has it when it has none. The downgrade cannot split them again.

Revision ID: 0003
Revises: 0002
"""

import re
import uuid
from collections import defaultdict

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

# The arXiv ID a Paper was held by: its ID without the Version, then the Version, such as v2. A copy of the pattern in
# src/schemas/arxiv/paper.py, as a migration must keep doing what it did when src/ changes.
VERSIONED_ARXIV_ID = re.compile(r"(?P<paper>.+?)v(?P<version>\d+)")

papers = sa.table("papers", sa.column("id"), sa.column("arxiv_id"), sa.column("version"), sa.column("pdf_processed"))

# A Paper's Content and processing state, which only a parsed Version has.
TAKE_CONTENT = sa.text(
    """
    UPDATE papers
    SET (raw_text, sections, "references", parser_used, parser_metadata, pdf_processed, pdf_processing_date) = (
        SELECT raw_text, sections, "references", parser_used, parser_metadata, pdf_processed, pdf_processing_date
        FROM papers WHERE id = :earlier
    )
    WHERE id = :latest
    """
)


def upgrade() -> None:
    op.add_column("papers", sa.Column("version", sa.Integer(), nullable=True))
    connection = op.get_bind()

    # Each Paper's Versions held: the Version, the row and whether it has Content.
    held: defaultdict[str, list[tuple[int, uuid.UUID, bool]]] = defaultdict(list)
    for row_id, held_arxiv_id, processed in connection.execute(sa.select(papers.c.id, papers.c.arxiv_id, papers.c.pdf_processed)):
        match = VERSIONED_ARXIV_ID.fullmatch(held_arxiv_id)
        if match is None:
            raise ValueError(f"Paper {held_arxiv_id} is held without its Version")
        held[match["paper"]].append((int(match["version"]), row_id, processed))

    for arxiv_id, versions in held.items():
        newest_first = sorted(versions, key=lambda held_version: held_version[0], reverse=True)
        (version, latest_row, latest_processed), *earlier = newest_first
        if not latest_processed:
            row_with_content = next((row_id for _, row_id, processed in earlier if processed), None)
            if row_with_content is not None:
                connection.execute(TAKE_CONTENT, {"earlier": row_with_content, "latest": latest_row})
        if earlier:
            connection.execute(papers.delete().where(papers.c.id.in_([row_id for _, row_id, _ in earlier])))
        connection.execute(papers.update().where(papers.c.id == latest_row).values(arxiv_id=arxiv_id, version=version))

    op.alter_column("papers", "version", nullable=False)


def downgrade() -> None:
    op.execute("UPDATE papers SET arxiv_id = arxiv_id || 'v' || version")
    op.drop_column("papers", "version")
