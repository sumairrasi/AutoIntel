import asyncio
from fastapi import APIRouter, HTTPException,status
from typing import Dict, List
from fastapi import APIRouter, HTTPException
import pytz
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, Depends
from app.db.database import get_db
from app.db.models import Document, Status, Token
from sqlalchemy import select, update
from app.utils.logging_config import get_logger
from sqlalchemy import select
from app.utils.token_utils import get_folder_storage_usage
from fastapi import APIRouter, Depends, HTTPException
from app.config.constant import VECTOR_DB,EMBEDDING_MODEL,RAG_MODEL,FOLDER_ID
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import Request
from sqlalchemy import func
from app.tasks import inject_verified_documents_task

router = APIRouter(prefix="/ocr", tags=["File Extract"])
logger = get_logger("ocr")


ist_now = datetime.now(pytz.timezone("Asia/Kolkata"))



@router.post("/documents/verified/trigger")
async def trigger_verified_documents_injection():
    task = inject_verified_documents_task.delay()
    return {"task_id": task.id, "status": "queued"}


@router.get("/chunk-count/{pdf_id}")
async def get_chunk_count(request: Request, pdf_id: int, db: AsyncSession = Depends(get_db)):
    logger.info(f"Endpoint: {request.url.path} Request received → get_chunk_count for pdf_id={pdf_id}")

    result = await db.execute(
        select(Token.chunk_count).where(Token.document_id == pdf_id)
    )
    chunk_count = result.scalar_one_or_none()

    if chunk_count is None:
        logger.warning(f"Endpoint: {request.url.path} No chunk data found for pdf_id={pdf_id}")
        raise HTTPException(status_code=404, detail="No chunk data found for this PDF")

    logger.info(f"Endpoint: {request.url.path} Chunk count for pdf_id={pdf_id} → {chunk_count}")
    return {
        "document_id": pdf_id,
        "chunk_count": chunk_count
    }




@router.get("/system-info")
async def get_system_info(request: Request, db: AsyncSession = Depends(get_db)):
    logger.info(f"Endpoint: {request.url.path} Request received → get_system_info")

    # Get last injected time
    result = await db.execute(
        select(func.max(Document.injected_time)).where(Document.status == Status.INJECTED)
    )
    last_updated = result.scalar_one_or_none()
    last_updated_str = last_updated.strftime("%m/%d/%Y, %I:%M:%S %p") if last_updated else None
    logger.info(f"Endpoint: {request.url.path} Last injected time → {last_updated_str}")

    # Get folder storage usage
    try:
        storage_info = get_folder_storage_usage(FOLDER_ID)
        logger.info(f"Endpoint: {request.url.path} Storage info retrieved successfully")
    except Exception as e:
        storage_info = {"error": str(e)}
        logger.error(f"Endpoint: {request.url.path} Failed to get storage info → {str(e)}")

    return {
        "vector_database": VECTOR_DB,    
        "embedding_model": EMBEDDING_MODEL,
        "rag_model": RAG_MODEL,
        "last_updated": last_updated_str,
        "storage_info": storage_info,
    }
