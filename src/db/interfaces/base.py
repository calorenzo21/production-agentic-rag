from abc import ABC, abstractmethod
from contextlib import AbstractContextManager

from sqlalchemy.orm import Session


class BaseDatabase(ABC):
    """Base class for database operations."""

    @abstractmethod
    def startup(self) -> None:
        """Initialize the database connection."""

    @abstractmethod
    def teardown(self) -> None:
        """Close the database connection."""

    @abstractmethod
    def get_session(self) -> AbstractContextManager[Session]:
        """Get a database session."""
