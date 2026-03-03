import asyncio
from typing import Optional
from contextlib import contextmanager
from datetime import datetime, timezone
from sqlalchemy import create_engine, Column, Integer, String, DateTime, JSON
from sqlalchemy.orm import declarative_base, sessionmaker
from dotenv import load_dotenv
import os

load_dotenv()
# 1. Configuration & Engine Setup
DB_URL = os.getenv("POSTGRES_DB_URL")
engine = create_engine(DB_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
Base = declarative_base()

# 2. Model Definition
class EventLog(Base):
    __tablename__ = 'tahoma_events'

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    event_type = Column(String, nullable=False)
    context_id = Column(String, nullable=False)
    page_id = Column(String, nullable=True)
    details = Column(JSON, nullable=True)
    s3_url = Column(String, nullable=True)

# 3. Context Manager for Sessions
@contextmanager
def get_db_session():
    """Provides a transactional scope around a series of operations."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

# 4. Initialization (Call this once when your app starts)
def init_db():
    Base.metadata.create_all(bind=engine)

# 5. Core Logging Logic
def _sync_send_to_log(event_type: str, context_id: str, page_id: Optional[str] = None, details: Optional[dict] = None, s3_url: Optional[str] = None):
    try:
        with get_db_session() as session:
            new_event = EventLog(
                event_type=event_type,
                context_id=context_id,
                page_id=page_id,
                details=details,
                s3_url=s3_url
            )
            session.add(new_event)
            # No need for session.commit() here; the context manager handles it!
    except Exception as e:
        print(f"⚠️ Failed to log event '{event_type}': {e}")

async def send_to_log(event_type: str, context_id: str, page_id: Optional[str] = None, details: Optional[dict] = None, s3_url: Optional[str] = None):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _sync_send_to_log, event_type, context_id, page_id, details, s3_url)


async def main():
    init_db()
    await send_to_log("test", "123", "456", {"hello": "world"})

if __name__ == '__main__':
    asyncio.run(main())
