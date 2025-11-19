from sqlmodel import SQLModel, create_engine, Session
import os
from typing import Generator
from sqlalchemy import text

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./dev.db")
connect_args = {} if "postgresql" in DATABASE_URL else {"check_same_thread": False}
engine = create_engine(DATABASE_URL, echo=False, connect_args=connect_args)

def init_db():
    # create tables if not exists
    SQLModel.metadata.create_all(engine)
    # post-create: for Postgres add columns if they don't exist (safe)
    try:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS plan VARCHAR DEFAULT 'free'"))
            conn.execute(text("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS storage_used_bytes BIGINT DEFAULT 0"))
            conn.commit()
    except Exception:
        # ignore if not postgres or permission issues; create_all covers new installations
        pass

def get_session() -> Generator:
    with Session(engine) as session:
        yield session
