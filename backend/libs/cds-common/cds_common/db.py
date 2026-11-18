from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


class Database:
    def __init__(self, url: str, *, application_name: str = "cds"):
        self.engine: Engine = create_engine(
            url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            connect_args={"application_name": application_name},
        )
        self.session_factory = sessionmaker(self.engine, expire_on_commit=False)

    def session(self) -> Session:
        return self.session_factory()


def get_session(request: Request) -> Iterator[Session]:
    """FastAPI dependency: one session per request. Use cases commit explicitly."""
    db: Database = request.app.state.db
    with db.session() as session:
        yield session
