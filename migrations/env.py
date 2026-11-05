from alembic import context
from sqlalchemy import create_engine
from src import models  # noqa: F401  (registers the tables on Base.metadata)
from src.config import get_settings
from src.db.interfaces.postgresql import Base

engine = create_engine(get_settings().postgres_database_url)

with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()

engine.dispose()
