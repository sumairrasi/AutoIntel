import io
import os
import fitz  
import pdfplumber
from googleapiclient.discovery import Resource
from fastapi import HTTPException,UploadFile


MAX_FILE_SIZE_MB = 40
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

def is_valid_pdf(file_data: bytes) -> bool:
    """Check if the file is a valid PDF using PyMuPDF."""
    try:
        fitz.open(stream=io.BytesIO(file_data), filetype="pdf").close()
        return True
    except Exception:
        return False


def is_duplicate_in_drive(filename: str, folder_id: str, drive_service: Resource) -> bool:
    """Check if a file with the same name already exists in the Drive folder."""
    query = f"name = '{filename}' and '{folder_id}' in parents and trashed = false"
    existing_files = drive_service.files().list(q=query, fields="files(id)").execute()
    return bool(existing_files.get("files"))


def detect_pdf_type(file_data: bytes) -> str:
    """
    Classify PDF as 'normal' (text-based or searchable) or 'scanned_image' (image-only).
    """
    try:
        with pdfplumber.open(io.BytesIO(file_data)) as pdf:
            text = ''.join((page.extract_text() or '') for page in pdf.pages[:2]).strip()
    except Exception:
        text = ''

    try:
        doc = fitz.open(stream=file_data, filetype="pdf")
        image_count = sum(len(page.get_images(full=True)) for page in doc[:2])
        doc.close()
    except Exception:
        image_count = 0

    if text and image_count == 0:
        return "normal"               # Pure text PDF
    elif not text and image_count > 0:
        return "scanned_image"        # Pure image PDF
    elif text and image_count > 0:
        return "normal"               # Searchable scanned PDF (OCR layer present)
    else:
        return "Unknown or empty PDF"
    

def get_pdf_page_count(file_data: bytes) -> int:
    """Return the number of pages in a PDF."""
    with fitz.open(stream=io.BytesIO(file_data), filetype="pdf") as doc:
        return doc.page_count


def validate_max_file_count(files: list, max_count: int = 10):
    """Raise HTTPException if number of files exceeds limit."""
    if len(files) > max_count:
        raise HTTPException(
            status_code=400,
            detail=f"You can upload a maximum of {max_count} files at a time. You uploaded {len(files)}."
        )

def get_non_pdf_files(files: list) -> list:
    """
    Return list of non-PDF files with their name and format.
    """
    non_pdfs = []
    for file in files:
        filename = file.filename
        ext = os.path.splitext(filename)[1].lower()
        if ext != ".pdf":
            non_pdfs.append({"filename": filename, "format": ext.lstrip(".")})
    return non_pdfs


def get_encrypted_pdfs(files: list) -> list:
    """
    Return a list of encrypted (password-protected) PDFs.
    """
    encrypted_pdfs = []
    for file in files:
        try:
            file_data = file.file.read()
            file.file.seek(0)  # Reset for further use
            with fitz.open(stream=file_data, filetype="pdf") as doc:
                if doc.is_encrypted:
                    encrypted_pdfs.append({"filename": file.filename})
        except Exception:
            continue  # If unreadable, ignore (already handled elsewhere)
    return encrypted_pdfs

def get_oversized_pdfs(files: list) -> list:
    """Return a list of PDFs larger than the allowed size (40 MB)."""
    oversized = []
    for file in files:
        file.file.seek(0, os.SEEK_END)
        size = file.file.tell()
        file.file.seek(0)
        if size > MAX_FILE_SIZE_BYTES:
            oversized.append({"filename": file.filename, "size_mb": round(size / (1024 * 1024), 2)})
    return oversized



def validate_pdf_uploads(files: list[UploadFile]):
    """
    Validate uploaded files:
    - Only PDFs allowed
    - No more than `max_count` files
    - Reject encrypted PDFs
    - Reject oversized PDFs (>40 MB)
    """
    # validate_max_file_count(files, max_count)

    non_pdfs = get_non_pdf_files(files)
    if non_pdfs:
        raise HTTPException(
            status_code=400,
            detail=f"Only PDF files are allowed. Invalid files: {non_pdfs}"
        )

    encrypted_pdfs = get_encrypted_pdfs(files)
    if encrypted_pdfs:
        raise HTTPException(
            status_code=400,
            detail=f"Encrypted PDFs are not allowed. Files: {encrypted_pdfs}"
        )

    # oversized_pdfs = get_oversized_pdfs(files)
    # if oversized_pdfs:
    #     raise HTTPException(
    #         status_code=400,
    #         detail=f"PDF size must not exceed {MAX_FILE_SIZE_MB} MB. Oversized files: {oversized_pdfs}"
    #     )

    # Final check: ensure all are valid PDFs
    for file in files:
        file_data = file.file.read()
        file.file.seek(0)
        if not is_valid_pdf(file_data):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid PDF file: {file.filename}"
            )

    return True
