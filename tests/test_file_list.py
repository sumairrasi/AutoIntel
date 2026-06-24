import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timedelta

from app.main import app
from app.db.models import Document, Token, Base, Status
from app.routers.file import get_db


# -------------------------------
# Test database (SQLite in-memory)
# -------------------------------
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture(scope="session")
async def engine():
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session(engine):
    async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with async_session() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture(scope="function")
async def client(db_session):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture(autouse=True)
async def setup_db(db_session: AsyncSession):
    doc1 = Document(
        filename="test1.pdf",
        doc_type="PDF",
        meta_data={"file_size_bytes": 1024, "file_size_mb": 0.000976, "page_count": 5},
        uploaded_time=datetime.utcnow() - timedelta(days=1),
        status=Status.UPLOADED,
        version=1,
    )
    doc2 = Document(
        filename="test2.pdf",
        doc_type="PDF",
        meta_data={"file_size_bytes": 2048, "file_size_mb": 0.00195, "page_count": 10},
        uploaded_time=datetime.utcnow() - timedelta(days=2),
        status=Status.UPLOADED,
        version=1,
    )
    db_session.add_all([doc1, doc2])
    await db_session.flush()

    token1 = Token(
        document_id=doc1.id,
        total_token=100.5,
        page_wise_token={"page1": 10, "page2": 20},
        chunk_count=5,
        cost=0.5,
    )
    token2 = Token(
        document_id=doc2.id,
        total_token=200.5,
        page_wise_token={"page1": 30, "page2": 40},
        chunk_count=10,
        cost=1.0,
    )
    db_session.add_all([token1, token2])
    await db_session.commit()


# -------------------------------
# Tests
# -------------------------------

@pytest.mark.asyncio
async def test_document_metadata_displayed(client: AsyncClient):
    """Verify document name, updated date, and file type are displayed."""
    response = await client.get("file/file/list")
    assert response.status_code == 200
    data = response.json()

    # Use the correct key 'files'
    files = data.get("files", [])
    assert len(files) > 0

    for file in files:
        assert "filename" in file
        assert file["filename"].endswith(".pdf")
        assert isinstance(file["filename"], str)

        assert "uploaded_time" in file
        assert isinstance(file["uploaded_time"], str)

        assert "doc_type" in file
        assert file["doc_type"] == "PDF"


@pytest.mark.asyncio
async def test_file_details_section(client: AsyncClient):
    """Verify file details section (size, pages, chunks)."""
    response = await client.get("file/file/list")
    assert response.status_code == 200
    data = response.json()

    files = data.get("files", [])
    assert len(files) > 0

    for file in files:
        meta = file.get("meta_data", {})
        assert "file_size_bytes" in meta
        assert "file_size_mb" in meta
        assert "page_count" in meta
        assert isinstance(meta["file_size_bytes"], int)
        assert isinstance(meta["file_size_mb"], float)
        assert isinstance(meta["page_count"], int)

        tokens = file.get("tokens", [])
        assert isinstance(tokens, list)
        for t in tokens:
            assert "chunk_count" in t
            assert isinstance(t["chunk_count"], int)
            assert t["chunk_count"] >= 0
