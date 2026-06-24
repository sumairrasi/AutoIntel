from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from app.config.constant import DATABASE_SYNC_URL,DATABASE_URL

SYNC_DATABASE_URL = DATABASE_URL.replace("asyncpg", "psycopg2")

# Create sync engine
engine = create_engine(
    SYNC_DATABASE_URL,
    echo=True,
    pool_size=10,
    max_overflow=20,
)

# Session factory
SessionLocal = sessionmaker(
    bind=engine,
    class_=Session,
    expire_on_commit=False,
)

def get_db():
    with SessionLocal() as session:
        yield session