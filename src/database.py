from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy.orm import Session
from src.db.factory import make_database
from src.db.interfaces.base import BaseDatabase

# Global database instance
_database: BaseDatabase | None = None


def get_database() -> BaseDatabase:
    """Get or create database instance."""
    global _database
    if _database is None:
        _database = make_database()
    return _database


@contextmanager
def get_db_session() -> Generator[Session, None, None]:
    """Get a database session context manager."""
    database = get_database()
    with database.get_session() as session:
        yield session
