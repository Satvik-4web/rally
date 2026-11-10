from pathlib import Path
from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker
from app.config import settings

if settings.RALLY_DATABASE_URL.startswith("sqlite:///"):
    db_file = settings.RALLY_DATABASE_URL.replace("sqlite:///", "")
    Path(db_file).parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    settings.RALLY_DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in settings.RALLY_DATABASE_URL else {},
)


@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if "sqlite" in settings.RALLY_DATABASE_URL:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        try:
            cursor.execute("PRAGMA table_info(ledger_events)")
            cols = {row[1] for row in cursor.fetchall()}
            if cols and "previous_hash" not in cols:
                cursor.execute("ALTER TABLE ledger_events ADD COLUMN previous_hash VARCHAR(64) DEFAULT 'GENESIS'")
            if cols and "entry_hash" not in cols:
                cursor.execute("ALTER TABLE ledger_events ADD COLUMN entry_hash VARCHAR(64)")
            dbapi_connection.commit()
        except Exception:
            pass
        cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()