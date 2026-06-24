# tests/test_incorrect_metadata.py
# Purpose: Verify incorrect file metadata (negative) — extract_file_metadata returns a non-dict.

import os
import io
import types
import importlib
import pytest
from fastapi.testclient import TestClient


def _make_tiny_pdf() -> bytes:
    # Minimal valid-looking PDF; structural checks are stubbed anyway.
    header = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n"
    body = (
        b"1 0 obj<<>>endobj\n"
        b"xref\n0 2\n0000000000 65535 f \n0000000010 00000 n \n"
        b"trailer<<>>\nstartxref\n0\n%%EOF\n"
    )
    return header + body


@pytest.fixture
def app_and_upload_module(monkeypatch):
    """
    Import the FastAPI app AFTER setting an env flag that prevents
    importing the OCR/Qdrant router (which touches network at import time).
    Then stub dependencies on the upload route.
    """
    # Import after env var is set
    mod_main = importlib.import_module("app.main")
    app = getattr(mod_main, "app")
    upload_module = importlib.import_module("app.routers.file")

    # ---- Patch external deps for /file/upload_file ----

    # Auth/GDrive: valid creds
    monkeypatch.setattr(
        upload_module,
        "load_credentials",
        lambda: types.SimpleNamespace(valid=True),
        raising=True,
    )

    # Stub Google Drive build().files().create(...).execute()
    class _DummyCreate:
        def __init__(self, body=None, media_body=None, fields=None):
            self.body = body
            self.media_body = media_body
            self.fields = fields

        def execute(self):
            # Return a Drive-like payload
            return {"id": "drive-file-123", "name": self.body.get("name")}

    class _DummyFiles:
        def create(self, *args, **kwargs):
            return _DummyCreate(*args, **kwargs)

    class _DummyDrive:
        def files(self):
            return _DummyFiles()

    monkeypatch.setattr(upload_module, "build", lambda *a, **k: _DummyDrive(), raising=True)

    # Pre-check helpers: pass-throughs
    monkeypatch.setattr(upload_module, "validate_max_file_count", lambda files: None, raising=True)
    monkeypatch.setattr(upload_module, "get_non_pdf_files", lambda files: [], raising=True)
    monkeypatch.setattr(upload_module, "get_encrypted_pdfs", lambda files: [], raising=True)

    # Structural checks: OK → we hit metadata path
    monkeypatch.setattr(upload_module, "is_valid_pdf", lambda _: True, raising=True)
    monkeypatch.setattr(upload_module, "get_pdf_page_count", lambda _: 1, raising=True)
    monkeypatch.setattr(upload_module, "verify_pdf_has_content", lambda _: (True, None), raising=True)
    monkeypatch.setattr(upload_module, "detect_pdf_type", lambda _: "GENERIC", raising=True)
    monkeypatch.setattr(upload_module, "is_duplicate_in_drive", lambda *a, **k: False, raising=True)

    try:
        monkeypatch.setattr(upload_module, "FOLDER_ID", "dummy-folder-id", raising=False)
    except Exception:
        pass

    # *** The negative: metadata is NOT a dict ***
    monkeypatch.setattr(upload_module, "extract_file_metadata", lambda path: "NOT_A_DICT", raising=True)

    # Capture store_document_metadata → must NOT be called due to earlier failure
    captured = {"calls": 0}

    async def _fake_store_document_metadata(*args, **kwargs):
        captured["calls"] += 1
        return types.SimpleNamespace(id=777)

    monkeypatch.setattr(upload_module, "store_document_metadata", _fake_store_document_metadata, raising=True)
    upload_module._captured_incorrect_meta = captured

    # DB dependency override (route shouldn't reach here, but keep safe)
    from app.db.database import get_db

    async def _dummy_db():
        class _Dummy:
            async def commit(self): pass
            async def close(self): pass
        try:
            yield _Dummy()
        finally:
            pass

    app.dependency_overrides[get_db] = _dummy_db

    return app, upload_module


def test_upload_incorrect_file_metadata_negative(app_and_upload_module):
    app, upload_module = app_and_upload_module
    client = TestClient(app, raise_server_exceptions=False)

    pdf_bytes = _make_tiny_pdf()
    files = [("files", ("meta-bad.pdf", io.BytesIO(pdf_bytes), "application/pdf"))]
    resp = client.post("/file/upload_file", files=files)
    assert resp.status_code == 500, resp.text

    ctype = resp.headers.get("content-type", "")
    if ctype.startswith("application/json"):
        body = resp.json()
        # default FastAPI 500 handler would be {"detail": "Internal Server Error"}
        assert "detail" in body
    else:
        # Starlette default: plain text
        assert "Internal Server Error" in resp.text

    # Ensure no DB persistence occurred
    captured = getattr(upload_module, "_captured_incorrect_meta")
    assert captured["calls"] == 0
