"""
Single shared engine/session factory. Every service and page imports
`get_session()` from here — this is what makes "one database, one data
model" actually true across modules instead of each page opening its own
connection.

Upgrading to PostgreSQL later = change DATABASE_URL in .env only.
Nothing in models.py, services/, or pages/ needs to change (see README
"Nâng cấp SQLite → PostgreSQL" section).
"""
from contextlib import contextmanager
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session

import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import settings

_connect_args = {"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=_connect_args,
    pool_pre_ping=True,
    future=True,
)

if settings.DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)


@contextmanager
def get_session():
    """Context-managed session: `with get_session() as db: ...` — commits on
    success, rolls back on exception, always closes."""
    session: Session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
