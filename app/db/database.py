import os
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from typing import AsyncGenerator
from app.config.constant import DATABASE_URL

# create async engine
engine: AsyncEngine = create_async_engine(
    DATABASE_URL,
    echo=True,   
    pool_size=10,
    max_overflow=20,
)

# create session factory
AsyncSessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session