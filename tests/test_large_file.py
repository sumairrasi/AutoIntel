# conftest.py
import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning,
                        message=r".*SwigPy(Packed|Object).*has no __module__ attribute")
warnings.filterwarnings("ignore", category=DeprecationWarning,
                        message=r".*swigvarlink.*has no __module__ attribute")

warnings.filterwarnings("ignore", category=DeprecationWarning,
                        message=r".*class-based `config` is deprecated, use ConfigDict instead.*", module=r"pydantic\._internal\._config")
warnings.filterwarnings("ignore", category=UserWarning,
                        message=r".*Valid config keys have changed in V2.*", module=r"pydantic\._internal\._config")

warnings.filterwarnings("ignore", category=DeprecationWarning,
                        message=r".*on_event is deprecated, use lifespan event handlers instead.*", module=r"fastapi\..*")
warnings.filterwarnings("ignore", category=DeprecationWarning,
                        message=r".*`regex` has been deprecated, please use `pattern` instead.*", module=r"fastapi\..*")


import io
import time
import types
import pytest
from fastapi.testclient import TestClient

# === CHANGE THESE TO MATCH YOUR CODEBASE ===
from app.main import app                       # FastAPI app
import app.routers.file as upload_module     # Module where `upload_files` route lives
from app.db.database import get_db                      # Dependency used by the route
# ===========================================


@pytest.fixture(autouse=True)
def _patch_dependencies(monkeypatch):
    """
    Patch external dependencies to avoid network/IO and ensure we stay on the
    validation path only. All patches are scoped to this test module.
    """

    # 1) Make credentials "valid" and stub Google Drive build (will not be used after validation fails).
    monkeypatch.setattr(
        upload_module,
        "load_credentials",
        lambda: types.SimpleNamespace(valid=True),
        raising=True,
    )

    # Stub google drive `build` to a harmless object
    class _DummyFilesApi:
        def create(self, *args, **kwargs):
            raise AssertionError("Drive upload should not be attempted for oversized files.")

    class _DummyDriveService:
        def files(self):
            return _DummyFilesApi()

    monkeypatch.setattr(
        upload_module,
        "build",
        lambda *args, **kwargs: _DummyDriveService(),
        raising=True,
    )

    # 2) Ensure pre-check helpers are no-ops / pass
    monkeypatch.setattr(upload_module, "validate_max_file_count", lambda files: None, raising=True)
    monkeypatch.setattr(upload_module, "get_non_pdf_files", lambda files: [], raising=True)
    monkeypatch.setattr(upload_module, "get_encrypted_pdfs", lambda files: [], raising=True)

    # 3) Force PDF to be considered structurally valid (so we hit only the size check)
    monkeypatch.setattr(upload_module, "is_valid_pdf", lambda _: True, raising=True)
    monkeypatch.setattr(upload_module, "get_pdf_page_count", lambda _: 1, raising=True)
    monkeypatch.setattr(upload_module, "verify_pdf_has_content", lambda _: (True, None), raising=True)
    monkeypatch.setattr(upload_module, "detect_pdf_type", lambda _: "GENERIC", raising=True)

    # 4) Folder id constant (if referenced later; harmless here)
    try:
        monkeypatch.setattr(upload_module, "FOLDER_ID", "dummy-folder-id", raising=False)
    except Exception:
        pass

    # 5) DB dependency override (the route won't reach DB on validation failure, but keep it safe)
    async def _dummy_db():
        class _DummySession:
            pass
        yield _DummySession()

    app.dependency_overrides[get_db] = _dummy_db


def _make_large_validish_pdf_bytes(size_mb: int = 30) -> bytes:
    """
    Create a minimal 'valid-looking' PDF byte sequence larger than `size_mb`.
    Since we monkeypatch `is_valid_pdf` to return True, structural fidelity is not critical.
    """
    header = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n"
    # Minimal objects/xref/trailer with EOF marker
    core = (
        b"1 0 obj<<>>endobj\n"
        b"xref\n0 2\n0000000000 65535 f \n0000000010 00000 n \n"
        b"trailer<<>>\nstartxref\n0\n%%EOF\n"
    )
    target_bytes = size_mb * 1024 * 1024
    pad_len = max(0, target_bytes - len(header) - len(core))
    return header + (b"0" * pad_len) + core


def test_upload_rejects_very_large_pdf_quickly():
    """
    GIVEN a single PDF > 25MB (MAX_FILE_SIZE_MB),
    WHEN posting to /upload_file,
    THEN respond with 400 status including 'oversized_files' entry,
         and complete within an acceptable latency budget.
    """
    client = TestClient(app)

    pdf_bytes = _make_large_validish_pdf_bytes(size_mb=30)  # 30 MB > 25 MB limit
    files = [
        ("files", ("big.pdf", io.BytesIO(pdf_bytes), "application/pdf"))
    ]

    # Performance budget (seconds). Choose a conservative threshold for CI stability.
    max_latency_seconds = 1.5

    t0 = time.perf_counter()
    resp = client.post("/file/upload_file", files=files)

    elapsed = time.perf_counter() - t0

    # Status and shape
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert "detail" in body, body
    detail = body["detail"]
    assert "oversized_files" in detail, detail
    assert detail.get("invalid_pdf_structure") in (None, [])  # should not be set on this path
    assert detail.get("page_limit_exceeded") in (None, [])    # should not be set on this path

    oversized = detail["oversized_files"]
    assert isinstance(oversized, list) and oversized, detail
    assert any(item.get("filename") == "big.pdf" for item in oversized), oversized

    # Size sanity (the route rounds to 2 decimals; just check >= 25)
    size_vals = [item.get("size_mb", 0) for item in oversized if item.get("filename") == "big.pdf"]
    assert size_vals and size_vals[0] >= 25.0, oversized

    # Performance assertion
    assert elapsed <= max_latency_seconds, f"Validation took too long: {elapsed:.3f}s > {max_latency_seconds:.3f}s"
