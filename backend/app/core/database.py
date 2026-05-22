import time
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import OperationalError

from app.core.config import settings
from app.models.base import Base

engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db(retries: int = 30, delay: float = 2.0):
    last_error: Exception | None = None
    for _ in range(retries):
        try:
            Base.metadata.create_all(bind=engine)
            return
        except OperationalError as exc:
            last_error = exc
            time.sleep(delay)
    raise RuntimeError(f"Database is not ready: {last_error}")
