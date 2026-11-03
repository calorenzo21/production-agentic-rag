from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """The metadata Alembic compares against; every table model inherits from this."""
