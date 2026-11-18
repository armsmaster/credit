import os

from alembic import context
from sqlalchemy import create_engine

VERSION_SCHEMA = "curves"


def run_migrations_online() -> None:
    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as connection:
        context.configure(connection=connection, version_table_schema=VERSION_SCHEMA)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
