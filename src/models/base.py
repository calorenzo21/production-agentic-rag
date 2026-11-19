from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class of every model, whose tables Alembic reads from Base.metadata."""
