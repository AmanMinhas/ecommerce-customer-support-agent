from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    # Request resource management: provide a session and close it on completion.
    # Services commit writes explicitly; successful requests are not auto-committed.
    with SessionLocal() as session:
        try:
            yield session
        except Exception:
            # Roll back uncommitted work before propagating the original error.
            session.rollback()
            raise

