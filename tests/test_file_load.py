import io
import pytest
from unittest.mock import MagicMock, patch
from httpx import AsyncClient, ASGITransport
from app.main import app  # FastAPI app

@pytest.mark.asyncio
async def test_view_pdf_file_success():
    """
    Verify that /view_files/{file_id} loads successfully and returns PDF.
    """

    # Mock credentials
    mock_creds = MagicMock()
    mock_creds.valid = True

    # Mock Google Drive service
    mock_drive_service = MagicMock()

    # Fake metadata for a PDF file
    mock_drive_service.files().get().execute.return_value = {
        "name": "sample.pdf",
        "mimeType": "application/pdf",
    }

    # Fake download into BytesIO
    mock_request = MagicMock()
    mock_downloader = MagicMock()
    mock_downloader.next_chunk.side_effect = [(None, True)]  # one successful chunk
    mock_drive_service.files().get_media.return_value = mock_request

    # ✅ Patch the module where get_pdf_file is defined
    with patch("app.routers.file.load_credentials", return_value=mock_creds), \
         patch("app.routers.file.build", return_value=mock_drive_service), \
         patch("app.routers.file.MediaIoBaseDownload", return_value=mock_downloader):

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.get("file/view_files/12345")

            # ✅ Assertions
            assert response.status_code == 200
            assert response.headers["content-type"] == "application/pdf"
            assert "inline; filename=" in response.headers["content-disposition"]
