from alembic import context
from sqlalchemy import create_engine

from paper_curator.database import Base
from paper_curator.logs import configure_logging
from paper_curator.settings import Settings

settings = Settings()
configure_logging(settings.log)
engine = create_engine(settings.postgres.url)

with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()

engine.dispose()
