import fitz  
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime
from typing import Dict, Optional,Tuple
import shutil
from app.db.models import Document, Status,ChatUserHistory,DriveFolder,LLMConfig,ChatUser,ConfigFlag
from app.config.constant import SCOPES,SCOPES,THRESHOLD
from pathlib import Path
import string
import random
from sqlalchemy import select
import os
import yaml
from langchain_openai import ChatOpenAI
from app.utils.google_drive_utils import _scopes_list,save_creds,load_credentials

import pytz

IST = pytz.timezone("Asia/Kolkata")
NEGATIVE_THRESHOLD = THRESHOLD

os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1") 


THUMBNAIL_DIR = "thumbnails"
os.makedirs(THUMBNAIL_DIR, exist_ok=True)
ALPHANUM = string.ascii_letters + string.digits
CHAT_ID_LENGTH = 12
MAX_RETRIES = 5


def to_ist(dt: datetime) -> datetime:
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=pytz.UTC)  # assume stored UTC if naive
    return dt.astimezone(IST)



# def _scopes_list():
#     return SCOPES if isinstance(SCOPES, (list, tuple)) else str(SCOPES).split()

    

def generate_pdf_thumbnail(file_bytes: bytes, filename: str) -> str:
    """
    Generate a PNG thumbnail for the first page of a PDF,
    save it to disk, and return the relative path (no base_url).
    """
    import os, fitz

    thumb_filename = f"{filename.replace('.pdf', '')}_thumb.png"
    output_dir = "thumbnails"
    os.makedirs(output_dir, exist_ok=True)
    full_path = os.path.join(output_dir, thumb_filename)

    doc = fitz.open(stream=file_bytes, filetype="pdf")
    if len(doc) == 0:
        return None
    page = doc.load_page(0)
    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
    pix.save(full_path)

    # return relative path only
    return f"/thumbnails/{thumb_filename}"


def extract_file_metadata(file_path: str) -> Dict:
    metadata = {}

    try:
        file_size = os.path.getsize(file_path)
        metadata["file_size_bytes"] = file_size
        metadata["file_size_mb"] = round(file_size / (1024 * 1024), 2)  
        # Try to extract PDF version manually, using ASCII fallback
        with open(file_path, "rb") as f:
            first_line = f.readline()
            try:
                first_line_decoded = first_line.decode("utf-8").strip()
            except UnicodeDecodeError:
                first_line_decoded = first_line.decode("latin-1").strip()  # more forgiving fallback

            if first_line_decoded.startswith("%PDF-"):
                metadata["pdf_version"] = first_line_decoded.replace("%PDF-", "")
            else:
                metadata["pdf_version"] = "unknown"

        # Extract using fitz
        with fitz.open(file_path) as doc:
            fitz_meta = doc.metadata or {}

            metadata.update({
                "title": fitz_meta.get("title"),
                "author": fitz_meta.get("author"),
                "subject": fitz_meta.get("subject"),
                "keywords": fitz_meta.get("keywords"),
                "creator": fitz_meta.get("creator"),
                "producer": fitz_meta.get("producer"),
                "creationDate": fitz_meta.get("creationDate"),
                "modDate": fitz_meta.get("modDate"),
                "page_count": doc.page_count,
                "is_encrypted": doc.is_encrypted,
                "needs_pass": doc.needs_pass,
                "page_width": doc[0].rect.width if doc.page_count > 0 else None,
                "page_height": doc[0].rect.height if doc.page_count > 0 else None,
            })

    except Exception as e:
        metadata["error"] = f"Failed to extract metadata: {str(e)}"

    return metadata

# === utils/db_ops.py ===
async def store_document_metadata(
    db: AsyncSession,
    filename: str,
    metadata: dict,
    doc_type: str = "normal",  
    status: Status = Status.UPLOADED,
    version: int = 1,
    file_id: Optional[str] = None
) -> Document:
    new_doc = Document(
        filename=filename,
        meta_data=metadata,
        uploaded_time=datetime.utcnow(),
        version=version,
        doc_type=doc_type,  
        status=status,
        file_id=file_id
    )
    db.add(new_doc)
    await db.commit()
    await db.refresh(new_doc)
    return new_doc

async def get_all_uploaded_documents(db: AsyncSession):
    stmt = select(Document).order_by(Document.uploaded_time.desc())
    result = await db.execute(stmt)
    documents = result.scalars().all()

    return [
        {
            "id": doc.id,
            "filename": doc.filename,
            "meta_data": doc.meta_data,
            "uploaded_time": doc.uploaded_time.isoformat(),
            "version": doc.version,
            "doc_type": doc.doc_type,   
            "status": doc.status.value,
        }
        for doc in documents
    ]



def delete_temp_dir(temp_dir_path: str):
    if os.path.exists(temp_dir_path) and os.path.isdir(temp_dir_path):
        try:
            shutil.rmtree(temp_dir_path)
            print(f"Temporary directory deleted: {temp_dir_path}")
        except Exception as e:
            print(f"Error deleting temporary directory: {e}")
    else:
        print(f"Temporary directory does not exist: {temp_dir_path}")
  



async def generate_unique_chat_id(db: AsyncSession) -> str:
    for _ in range(MAX_RETRIES):
        candidate = ''.join(random.choices(ALPHANUM, k=CHAT_ID_LENGTH))
        exists = await db.execute(select(ChatUser).where(ChatUser.chat_id == candidate))
        if not exists.scalars().first():
            return candidate
    raise RuntimeError("Could not generate a unique chat_id")

async def create_new_chat_session(db: AsyncSession, user_id: int) -> ChatUser:
    """
    Always inserts a NEW row in chat_users with the same user_id and a fresh, unique chat_id.
    Also inserts the matching chat_user_histories row. Safe under concurrency.
    """
    for attempt in range(MAX_RETRIES):
        cid = await generate_unique_chat_id(db)

        user_row = ChatUser(user_id=user_id, chat_id=cid)
        hist_row = ChatUserHistory(chat_id=cid, chat_history=[])

        db.add_all([user_row, hist_row])

        await db.commit()
        await db.refresh(user_row)
        return user_row





def verify_pdf_has_content(file_bytes: bytes) -> Tuple[bool, Optional[str]]:
    """
    Returns (is_valid, reason_if_invalid).
    Valid when: opens as PDF, has >=1 page, and at least one page has text or images.
    """
    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
    except Exception as e:
        return (False, f"Cannot open as PDF: {e}")

    try:
        if len(doc) == 0:
            return (False, "PDF has no pages.")

        for page in doc:
            text = page.get_text().strip()
            images = page.get_images(full=True)
            if text or images:
                return (True, None)

        return (False, "No text or image content found in PDF.")
    finally:
        doc.close()



async def get_chat_model(db: AsyncSession):
    result = await db.execute(select(LLMConfig).order_by(LLMConfig.id.desc()))
    config = result.scalars().first()
    if not config:
        raise ValueError("No config in database")

    return ChatOpenAI(
        temperature=config.temperature,
        frequency_penalty=config.frequency_penalty,
        presence_penalty=config.presence_penalty
    )



async def get_or_create_drive_folder(
    db: AsyncSession, drive_folder_id: str, name: str, parent_drive_id: Optional[str] = None
):

    result = await db.execute(
        select(DriveFolder).where(DriveFolder.folder_id == drive_folder_id)
    )
    folder = result.scalars().first()

    if not folder:
        folder = DriveFolder(
            folder_id=drive_folder_id,   # Google Drive folder id
            parent_drive_id=parent_drive_id,
            name=name
        )
        db.add(folder)
        await db.commit()
        await db.refresh(folder)

    return folder


def load_prompts(filename: str = "prompt.yaml") -> dict:
    """
    Load prompts from a YAML file in app/config/.

    Args:
        filename (str): Name of the YAML file (default: prompt.yaml)

    Returns:
        dict: Parsed prompts dictionary
    """
    # Get absolute path: app/config/prompt.yaml
    config_path = Path(__file__).resolve().parent.parent / "config" / filename
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)
    
    






async def update_negative_feedback_flag(db: AsyncSession):
    """
    Increment negative feedback count in ConfigFlag for every negative feedback.
    Set status True if count >= NEGATIVE_THRESHOLD.
    """
    # Get existing ConfigFlag
    result = await db.execute(select(ConfigFlag))
    flag = result.scalar_one_or_none()

    if not flag:
        # Create a new record if none exists
        flag = ConfigFlag(last_triggered_count=1, status=False)
        db.add(flag)
    else:
        # Increment count for every negative feedback
        flag.last_triggered_count += 1
        if flag.last_triggered_count >= NEGATIVE_THRESHOLD:
            flag.status = True

    await db.commit()
    await db.refresh(flag)
