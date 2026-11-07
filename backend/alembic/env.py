from alembic import context
from app import models  # noqa: F401 - populate metadata for comparison, never schema creation
from app.config import Settings
from app.db import Base, make_engine

target_metadata = Base.metadata


def run_migrations():
    url = Settings().database_url
    if context.is_offline_mode():
        context.configure(
            url=url,
            target_metadata=target_metadata,
            literal_binds=True,
            dialect_opts={"paramstyle": "named"},
        )
        with context.begin_transaction():
            context.run_migrations()
    else:
        engine = make_engine(url)
        with engine.connect() as connection:
            context.configure(
                connection=connection, target_metadata=target_metadata, compare_type=True
            )
            with context.begin_transaction():
                context.run_migrations()
        engine.dispose()


run_migrations()
