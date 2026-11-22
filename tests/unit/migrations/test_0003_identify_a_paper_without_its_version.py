"""
Migration 0003, which identifies a Paper by its arXiv ID without its Version (ADR 0004), on Papers held before it,
in a database of its own in the disposable PostgreSQL.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, make_url, text
from src.config import get_settings
from src.db.interfaces.base import BaseDatabase

ALEMBIC = Config(Path(__file__).parents[3] / "alembic.ini")


@pytest.fixture
def database_at_0002(migrated_database: BaseDatabase) -> Iterator[Engine]:
    """A database of its own at revision 0002, which the migrations reach through the settings, as they do in the stack."""
    server = make_url(get_settings().postgres_database_url)
    admin = create_engine(server, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text("CREATE DATABASE migration_0003"))
    url = server.set(database="migration_0003")
    try:
        with pytest.MonkeyPatch.context() as environment:
            environment.setenv("POSTGRES_DATABASE_URL", url.render_as_string(hide_password=False))
            get_settings.cache_clear()
            command.upgrade(ALEMBIC, "0002")
            engine = create_engine(url)
            yield engine
            engine.dispose()
    finally:
        get_settings.cache_clear()
        with admin.connect() as connection:
            connection.execute(text("DROP DATABASE migration_0003 WITH (FORCE)"))
        admin.dispose()


def hold(engine: Engine, arxiv_id: str, *, content: str | None = None) -> None:
    """Hold a Paper as the curator did before 0003, by its arXiv ID with its Version, with Content when given."""
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO papers (id, arxiv_id, title, authors, abstract, categories, published_date, pdf_url,
                                    raw_text, sections, pdf_processed)
                VALUES (gen_random_uuid(), :arxiv_id, :title, '["Ada Lovelace"]', 'An abstract.', '["cs.AI"]',
                        '2024-11-01 09:30', :pdf_url, :content, :sections, :processed)
                """
            ),
            {
                "arxiv_id": arxiv_id,
                "title": f"The title of {arxiv_id}",
                "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
                "content": content,
                "sections": None if content is None else f'[{{"title": "Introduction", "content": "{content}"}}]',
                "processed": content is not None,
            },
        )


def held(engine: Engine) -> list[tuple[str, int, str, str | None, bool]]:
    """Each Paper held: its arXiv ID, Version, title, raw text and processed state, by arXiv ID."""
    with engine.connect() as connection:
        rows = connection.execute(text("SELECT arxiv_id, version, title, raw_text, pdf_processed FROM papers ORDER BY arxiv_id"))
        return [tuple(row) for row in rows]


def test_upgrading_holds_each_paper_once_at_its_latest_version_with_that_versions_content(database_at_0002: Engine) -> None:
    hold(database_at_0002, "2411.00001v1", content="The Content of v1.")
    hold(database_at_0002, "2411.00001v2", content="The Content of v2.")
    hold(database_at_0002, "2411.00002v1")

    command.upgrade(ALEMBIC, "0003")

    assert held(database_at_0002) == [
        ("2411.00001", 2, "The title of 2411.00001v2", "The Content of v2.", True),
        ("2411.00002", 1, "The title of 2411.00002v1", None, False),
    ]


def test_upgrading_gives_a_latest_version_without_content_the_content_of_the_latest_version_that_has_it(
    database_at_0002: Engine,
) -> None:
    hold(database_at_0002, "2411.00001v1", content="The Content of v1.")
    hold(database_at_0002, "2411.00001v2", content="The Content of v2.")
    hold(database_at_0002, "2411.00001v3")

    command.upgrade(ALEMBIC, "0003")

    assert held(database_at_0002) == [("2411.00001", 3, "The title of 2411.00001v3", "The Content of v2.", True)]


def test_upgrading_refuses_a_paper_held_without_its_version(database_at_0002: Engine) -> None:
    hold(database_at_0002, "2411.00001")

    with pytest.raises(ValueError, match="2411.00001"):
        command.upgrade(ALEMBIC, "0003")


def test_downgrading_puts_each_papers_version_back_into_its_arxiv_id(database_at_0002: Engine) -> None:
    hold(database_at_0002, "2411.00001v1")
    hold(database_at_0002, "2411.00001v2")
    command.upgrade(ALEMBIC, "0003")

    command.downgrade(ALEMBIC, "0002")

    with database_at_0002.connect() as connection:
        assert list(connection.scalars(text("SELECT arxiv_id FROM papers"))) == ["2411.00001v2"]
