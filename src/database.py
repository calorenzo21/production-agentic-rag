import threading
from collections.abc import Generator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy.orm import Session
from src.db.factory import make_database
from src.db.interfaces.base import BaseDatabase

# lru_cache alone lets threads that arrive together each build a database, so they queue here.
_lock = threading.Lock()


@lru_cache
def _create_database() -> BaseDatabase:
    return make_database()


def get_database() -> BaseDatabase:
    """Get or create database instance, once per process even across threads."""
    with _lock:
        return _create_database()


@contextmanager
def get_db_session() -> Generator[Session, None, None]:
    """Get a database session context manager."""
    database = get_database()
    with database.get_session() as session:
        yield session
