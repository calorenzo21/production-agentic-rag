from collections.abc import Generator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session
from src.config import Settings, get_settings
from src.db.interfaces.base import BaseDatabase
from src.services.opensearch.client import OpenSearchClient


def get_database(request: Request) -> BaseDatabase:
    """Get database from the request state."""
    database: BaseDatabase = request.app.state.database
    return database


def get_db_session(database: Annotated[BaseDatabase, Depends(get_database)]) -> Generator[Session, None, None]:
    """Get database session dependency."""
    with database.get_session() as session:
        yield session


def get_opensearch_client(request: Request) -> OpenSearchClient:
    """Get OpenSearch client from the request state."""
    opensearch_client: OpenSearchClient = request.app.state.opensearch_client
    return opensearch_client


# Dependency annotations
SettingsDep = Annotated[Settings, Depends(get_settings)]
DatabaseDep = Annotated[BaseDatabase, Depends(get_database)]
SessionDep = Annotated[Session, Depends(get_db_session)]
OpenSearchDep = Annotated[OpenSearchClient, Depends(get_opensearch_client)]
