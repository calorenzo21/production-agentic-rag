from collections.abc import Generator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy.orm import Session
from src.db.factory import make_database
from src.db.interfaces.base import BaseDatabase


@lru_cache
def get_database() -> BaseDatabase:
    """Get or create database instance."""
    return make_database()


@contextmanager
def get_db_session() -> Generator[Session, None, None]:
    """Get a database session context manager."""
    database = get_database()
    with database.get_session() as session:
        yield session
