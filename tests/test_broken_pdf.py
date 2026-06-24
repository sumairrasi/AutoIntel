# tests/test_broken_pdf_verification.py
# Adjust import paths if your package layout differs.

import io
import types
import pytest
from fastapi.testclient import TestClient

from app.main import app
import app.routers.file as upload_module
from app.db.database import get_db


@pytest.fixture(autouse=True)
def _patch_dependencies(monkeypatch):
    """Patch external dependencies to force the negative (broken/missing) verification path."""

    # 1) Auth/GDrive: make creds valid, and stub Drive create().execute()
    monkeypatch.setattr(
        upload_module,
        "load_credentials",
        lambda: types.SimpleNamespace(valid=True),
        raising=True,
    )

    class _DummyCreateReq:
        def __init__(self, body=None, media_body=None, fields=None):
            self.body = body
            self.media_body = media_body
            self.fields = fields

        def execute(self):
            # mimic Drive response
            return {"id": "drive-file-123", "name": self.body.get("name")}

    class _DummyFilesApi:
        def create(self, *args, **kwargs):
            return _DummyCreateReq(*args, **kwargs)

    class _DummyDriveService:
        def files(self):
            return _DummyFilesApi()

    monkeypatch.setattr(upload_module, "build", lambda *_, **__: _DummyDriveService(), raising=True)

    # No duplicate in Drive
    monkeypatch.setattr(upload_module, "is_duplicate_in_drive", lambda *_, **__: False, raising=True)

    # 2) Pre-check helpers — pass through
    monkeypatch.setattr(upload_module, "validate_max_file_count", lambda files: None, raising=True)
    monkeypatch.setattr(upload_module, "get_non_pdf_files", lambda files: [], raising=True)
    monkeypatch.setattr(upload_module, "get_encrypted_pdfs", lambda files: [], raising=True)

    # 3) Make the PDF structurally "valid", small, and single-page
    monkeypatch.setattr(upload_module, "is_valid_pdf", lambda _: True, raising=True)
    monkeypatch.setattr(upload_module, "get_pdf_page_count", lambda _: 1, raising=True)

    # 4) Force verification to FAIL (broken/missing content)
    FAIL_REASON = "No extractable text or content found"
    monkeypatch.setattr(upload_module, "verify_pdf_has_content", lambda _: (False, FAIL_REASON), raising=True)

    # 5) Keep a simple doc type
    monkeypatch.setattr(upload_module, "detect_pdf_type", lambda _: "GENERIC", raising=True)

    # 6) Constant folder
    try:
        monkeypatch.setattr(upload_module, "FOLDER_ID", "dummy-folder-id", raising=False)
    except Exception:
        pass

    # 7) Capture calls to store_document_metadata to assert status=UPLOADED
    captured = {"calls": []}

    async def _fake_store_document_metadata(db, filename, metadata, doc_type, status, version=1, file_id=None):
        # record what was passed for inspection in the test
        captured["calls"].append(
            {
                "filename": filename,
                "doc_type": doc_type,
                "status": status,
                "file_id": file_id,
                "metadata": metadata,
            }
        )
        # return a dummy "Document" like object
        return types.SimpleNamespace(id=42, filename=filename)

    monkeypatch.setattr(upload_module, "store_document_metadata", _fake_store_document_metadata, raising=True)
    monkeypatch.setattr(upload_module, "_captured_store_calls", captured, raising=False)

    # 8) DB dependency override (kept minimal)
    async def _dummy_db():
        class _DummySession:
            async def commit(self): pass
            async def close(self): pass
        try:
            yield _DummySession()
        finally:
            pass

    app.dependency_overrides[get_db] = _dummy_db


def _make_small_validish_pdf_bytes() -> bytes:
    # tiny but valid-looking PDF (we stub structural checks anyway)
    header = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n"
    body = b"1 0 obj<<>>endobj\nxref\n0 2\n0000000000 65535 f \n0000000010 00000 n \ntrailer<<>>\nstartxref\n0\n%%EOF\n"
    return header + body


def test_upload_broken_missing_document_verification_negative():
    client = TestClient(app)

    pdf_bytes = _make_small_validish_pdf_bytes()
    files = [("files", ("broken.pdf", io.BytesIO(pdf_bytes), "application/pdf"))]

    resp = client.post("/file/upload_file", files=files)
    assert resp.status_code == 200, resp.text

    payload = resp.json()
    assert payload["status"] == "success"
    assert isinstance(payload["files"], list) and len(payload["files"]) == 1

    f0 = payload["files"][0]
    # The API should reflect verification failure
    assert f0["name"] == "broken.pdf"
    assert f0["verified"] is False
    assert "verification_reason" in f0 and f0["verification_reason"], f0

    # No "verified_changes" should be registered on failed verification
    assert payload.get("verified_changes") in ([], None)

    # Ensure DB write used Status.UPLOADED (not VERIFIED)
    captured = getattr(upload_module, "_captured_store_calls")
    assert captured["calls"], "store_document_metadata was not called"
    call = captured["calls"][0]

    # Status is the Enum from the router module
    assert call["status"] == upload_module.Status.UPLOADED
    assert call["filename"] == "broken.pdf"
    assert call["doc_type"] == "GENERIC"
    assert call["file_id"] == "drive-file-123"
