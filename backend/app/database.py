from sqlalchemy import create_engine, event
from sqlalchemy.pool import QueuePool
from sqlalchemy.orm import declarative_base, sessionmaker
from .config import settings

engine = create_engine(
    settings.DATABASE_URL,
    poolclass=QueuePool if "sqlite" in settings.DATABASE_URL else None,
    pool_size=25 if "sqlite" in settings.DATABASE_URL else 5,
    max_overflow=40 if "sqlite" in settings.DATABASE_URL else 10,
    connect_args={"check_same_thread": False, "timeout": 30.0} if "sqlite" in settings.DATABASE_URL else {}
)

# SQLite 开启外键级联、WAL 模式、64MB内存缓存与256MB mmap内存映射 I/O
if "sqlite" in settings.DATABASE_URL:
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA cache_size=-64000")
        cursor.execute("PRAGMA mmap_size=268435456")
        cursor.execute("PRAGMA temp_store=MEMORY")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
