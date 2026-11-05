import logging
from collections.abc import Generator
from contextlib import contextmanager

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from src.db.interfaces.base import BaseDatabase

logger = logging.getLogger(__name__)


class PostgreSQLSettings(BaseSettings):
    """PostgreSQL configuration settings."""

    database_url: str = Field(default="postgresql://rag_user@localhost:5432/rag_db", description="PostgreSQL database URL")
    echo_sql: bool = Field(default=False, description="Enable SQL query logging")
    pool_size: int = Field(default=20, description="Database connection pool size")
    max_overflow: int = Field(default=0, description="Maximum pool overflow")

    model_config = SettingsConfigDict(env_prefix="POSTGRES_")


class Base(DeclarativeBase):
    pass


class PostgreSQLDatabase(BaseDatabase):
    """PostgreSQL database implementation."""

    def __init__(self, config: PostgreSQLSettings):
        self.config = config
        self.engine: Engine | None = None
        self.session_factory: sessionmaker[Session] | None = None

    def startup(self) -> None:
        """Initialize the database connection."""
        try:
            # Log connection attempt
            host = self.config.database_url.split("@")[-1]
            logger.info(f"Attempting to connect to PostgreSQL at: {host}")

            self.engine = create_engine(
                self.config.database_url,
                echo=self.config.echo_sql,
                pool_size=self.config.pool_size,
                max_overflow=self.config.max_overflow,
                pool_pre_ping=True,  # Verify connections before use
            )

            self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

            # Test the connection
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
                logger.info("Database connection test successful")

            # Alembic owns the schema: the migrate service creates the tables before anything connects.
            logger.info(f"Database: {self.engine.url.database}")
            logger.info("Database connection established")

        except Exception as e:
            logger.error(f"Failed to initialize PostgreSQL database: {e}")
            raise

    def teardown(self) -> None:
        """Close the database connection."""
        if self.engine:
            self.engine.dispose()
            logger.info("PostgreSQL database connections closed")

    @contextmanager
    def get_session(self) -> Generator[Session, None, None]:
        """Get a database session."""
        if not self.session_factory:
            raise RuntimeError("Database not initialized. Call startup() first.")

        session = self.session_factory()
        try:
            yield session
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
