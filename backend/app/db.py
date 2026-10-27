"""Database connection (SQLAlchemy 2.0 + psycopg 3) for Neon."""
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from .config import settings


def _driver_url(url: str) -> str:
    """Neon gives postgresql://...; SQLAlchemy needs to be told to use psycopg 3."""
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


engine = create_engine(
    _driver_url(settings.database_url),
    pool_size=5,
    max_overflow=5,
    pool_pre_ping=True,       # Neon closes idle connections; test before use
    pool_recycle=240,
    connect_args={
        "connect_timeout": 20,    # a sleeping Neon compute can take a few seconds to wake
        "prepare_threshold": None,  # the Neon pooler (PgBouncer) must not see prepared statements
    },
)


@contextmanager
def session_scope():
    """One database transaction: commit when the block ends, roll back on any error."""
    with Session(engine) as session, session.begin():
        # Pooled connections reject startup options, so the timeout is set per transaction instead.
        session.execute(text("SELECT set_config('statement_timeout', '15000', true)"))
        yield session


def rows(session: Session, sql: str, **params) -> list[dict]:
    """Run a query and return the result as a list of dicts."""
    return [dict(r) for r in session.execute(text(sql), params).mappings()]
