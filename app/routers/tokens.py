from typing import Optional, Tuple
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func
from app.db.database import get_db
from app.db.models import ChatUser, Token, Document
from fastapi.responses import StreamingResponse
import csv
import io
from sqlalchemy import func
from sqlalchemy.orm import selectinload
from datetime import datetime
from sqlalchemy import extract
from starlette.concurrency import run_in_threadpool
from googleapiclient.discovery import build
from app.routers.file import _drive_list_children
from app.utils.token_utils import load_credentials

from app.config.constant import CLIENT_SECRET_FILE,SCOPES,REDIRECT_URI,TOKEN_FILE,FOLDER_ID

from app.utils.google_drive_utils import ( 
    FOLDER_MIME, 
    _scopes_list,
    save_creds,
    load_credentials,
    _fetch_all_files_recursive, 
    _drive_find_child_by_name, 
    delete_drive_folder_recursive, 
    _drive_create_folder, 
    _drive_list_children, 
    _drive_find_child_by_name,
    is_duplicate_in_parent,
    ensure_drive_path,
    _fetch_all_files_recursive
    )
from app.utils.logging_config import get_logger
router = APIRouter(prefix="/tokens", tags=["Tokens"])

logger = get_logger("tokens")




# FOLDER_MIME = "application/vnd.google-apps.folder"
 
# def _drive_list_children(drive_service, parent_id: str, mime_filter: Optional[str] = None, name_eq: Optional[str] = None):
#     q_parts = [f"'{parent_id}' in parents", "trashed=false"]
#     if mime_filter:
#         q_parts.append(f"mimeType='{mime_filter}'")
#     if name_eq:
#         safe_name = name_eq.replace("'", "\\'")
#         q_parts.append(f"name='{safe_name}'")
#     q = " and ".join(q_parts)
 
#     results = []
#     page_token = None
#     while True:
#         resp = drive_service.files().list(
#             q=q,
#             spaces="drive",
#             fields="nextPageToken, files(id, name, mimeType)",
#             pageToken=page_token,
#             includeItemsFromAllDrives=True,
#             supportsAllDrives=True,
#         ).execute()
#         results.extend(resp.get("files", []))
#         page_token = resp.get("nextPageToken")
#         if not page_token:
#             break
#     return results
 
# def _drive_find_child_by_name(drive_service, parent_id: str, name: str, require_folder: bool = False) -> Optional[dict]:
#     mime = FOLDER_MIME if require_folder else None
#     items = _drive_list_children(drive_service, parent_id, mime_filter=mime, name_eq=name)
#     return items[0] if items else None
 
# def _drive_create_folder(drive_service, name: str, parent_id: str) -> dict:
#     body = {"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]}
#     return drive_service.files().create(
#         body=body, fields="id, name, mimeType", supportsAllDrives=True
#     ).execute()
 
# def ensure_drive_path(drive_service, root_folder_id: str, brand: str, subpath: Optional[str]) -> Tuple[str, str]:
#     if not brand or not brand.strip():
#         raise HTTPException(status_code=400, detail="Brand folder name is required.")
#     brand = brand.strip()
#     if not root_folder_id:
#         raise HTTPException(status_code=500, detail="FOLDER_ID is not configured on server.")
 
#     brand_node = _drive_find_child_by_name(drive_service, root_folder_id, brand, require_folder=True)
#     if not brand_node:
#         brand_node = _drive_create_folder(drive_service, brand, root_folder_id)
 
#     parent_id = brand_node["id"]
#     normalized = ""
#     if subpath:
#         parts = [p.strip() for p in subpath.split("/") if p.strip()]
#         for part in parts:
#             existing = _drive_find_child_by_name(drive_service, parent_id, part, require_folder=True)
#             if not existing:
#                 existing = _drive_create_folder(drive_service, part, parent_id)
#             parent_id = existing["id"]
#         normalized = "/".join(parts)
#     return parent_id, normalized
 
# def is_duplicate_in_parent(drive_service, parent_id: str, filename: str) -> bool:
#     items = _drive_list_children(drive_service, parent_id, mime_filter=None, name_eq=filename)
#     return any(item.get("mimeType") != FOLDER_MIME for item in items)






# 1. View token usage for a specific PDF
@router.get("/usage/{pdf_id}")
async def get_token_usage(request: Request, pdf_id: int, db: AsyncSession = Depends(get_db)):
    logger.info(f"Endpoint: {request.url.path} Request received for pdf_id: {pdf_id}")

    query = await db.execute(
        select(Token).where(Token.document_id == pdf_id)
    )
    token = query.scalar_one_or_none()
    if not token:
        logger.warning(f"Endpoint: {request.url.path} Token usage not found for pdf_id: {pdf_id}")
        raise HTTPException(status_code=404, detail="Token usage not found for this PDF")
    
    logger.info(f"Endpoint: {request.url.path} Token usage fetched → document_id={token.document_id}, total_token={token.total_token}, chunk_count={token.chunk_count}, cost={token.cost}")

    return {
        "document_id": token.document_id,
        "total_token": token.total_token,
        "page_wise_token": token.page_wise_token,
        "chunk_count": token.chunk_count,
        "cost": token.cost
    }





@router.get("/token_usage/total")
async def get_total_token_usage(request: Request, db: AsyncSession = Depends(get_db)):
    logger.info(f"Endpoint: {request.url.path} Request received to fetch total token usage and stats")

    now = datetime.utcnow()
    current_year = now.year
    current_month = now.month

    # --------------------------
    # Token totals (all time)
    # --------------------------
    token_totals_row = await db.execute(
        select(
            func.coalesce(func.sum(Token.total_token), 0.0).label("total_tokens"),
            func.coalesce(func.sum(Token.cost), 0.0).label("total_cost"),
            func.coalesce(func.sum(Token.chunk_count), 0).label("total_chunks"),
        )
    )
    token_totals = token_totals_row.one()
    logger.info(f"Endpoint: {request.url.path} Total tokens={token_totals.total_tokens}, Total cost={token_totals.total_cost}, Total chunks={token_totals.total_chunks}")

    # Fetch all page_wise_token JSON blobs
    token_rows = await db.execute(select(Token.page_wise_token))
    all_page_wise_tokens = token_rows.scalars().all()

    total_chunk_tokens = 0.0
    for pwt in all_page_wise_tokens:
        if isinstance(pwt, dict):
            for v in pwt.values():
                try:
                    total_chunk_tokens += float(v)
                except:
                    pass
    logger.info(f"Endpoint: {request.url.path} Total chunk tokens calculated={total_chunk_tokens}")

    # --------------------------
    # Document counts and status
    # --------------------------
    doc_count_result = await db.execute(select(func.count(Document.id)))
    total_documents = doc_count_result.scalar_one()
    logger.info(f"Endpoint: {request.url.path} Total documents={total_documents}")

    status_counts_result = await db.execute(
        select(Document.status, func.count(Document.id)).group_by(Document.status)
    )
    status_counts = {status.value: count for status, count in status_counts_result.all()}
    logger.info(f"Endpoint: {request.url.path} Document status counts={status_counts}")

    # --------------------------
    # Monthly injected documents count
    # --------------------------
    injected_this_month_result = await db.execute(
        select(func.count(Document.id))
        .where(
            Document.status == "injected",
            Document.injected_time.isnot(None),
            extract('year', Document.injected_time) == current_year,
            extract('month', Document.injected_time) == current_month
        )
    )
    injected_this_month_count = injected_this_month_result.scalar_one()
    logger.info(f"Endpoint: {request.url.path} Injected documents this month={injected_this_month_count}")

    # --------------------------
    # Monthly document cost
    # --------------------------
    doc_monthly_cost_result = await db.execute(
        select(func.coalesce(func.sum(Token.cost), 0.0))
        .join(Document, Document.id == Token.document_id)
        .where(
            Document.status == "injected",
            Document.injected_time.isnot(None),
            extract('year', Document.injected_time) == current_year,
            extract('month', Document.injected_time) == current_month
        )
    )
    doc_monthly_cost = doc_monthly_cost_result.scalar_one()
    logger.info(f"Endpoint: {request.url.path} Document monthly cost={doc_monthly_cost}")

    # --------------------------
    # Monthly chat user cost
    # --------------------------
    chat_monthly_cost_result = await db.execute(
        select(func.coalesce(func.sum(ChatUser.total_cost), 0.0))
        .where(
            ChatUser.last_message_at.isnot(None),
            extract('year', ChatUser.last_message_at) == current_year,
            extract('month', ChatUser.last_message_at) == current_month
        )
    )
    chat_monthly_cost = chat_monthly_cost_result.scalar_one()
    logger.info(f"Endpoint: {request.url.path} Chat monthly cost={chat_monthly_cost}")

    # --------------------------
    # Calculate total monthly cost
    # --------------------------
    monthly_cost = float(doc_monthly_cost or 0.0) + float(chat_monthly_cost or 0.0)
    logger.info(f"Endpoint: {request.url.path} Total monthly cost={monthly_cost}")

    # --------------------------
    # Root folders count from Google Drive
    # --------------------------
    creds = load_credentials()
    drive_service = build("drive", "v3", credentials=creds)

    root_folders = await run_in_threadpool(
        _drive_list_children, drive_service, FOLDER_ID, FOLDER_MIME, None
    )
    root_folder_count = len(root_folders)
    logger.info(f"Endpoint: {request.url.path} Root folder count from Drive={root_folder_count}")

    # --------------------------
    # Return all totals
    # --------------------------
    return {
        "total_tokens": float(token_totals.total_tokens or 0.0),
        "total_cost": round(float(token_totals.total_cost or 0.0), 8),
        "total_chunks": int(token_totals.total_chunks or 0),
        "total_chunk_tokens": float(total_chunk_tokens),
        "total_documents": int(total_documents),
        "total_users": 12,
        "document_status_counts": {
            "uploaded": status_counts.get("uploaded", 0),
            "verified": status_counts.get("verified", 0),
            "injected": status_counts.get("injected", 0),
        },
        "injected_this_month": int(injected_this_month_count),
        "monthly_cost": round(monthly_cost, 8),
        "open_ai": True,
        "root_folder_count": root_folder_count
    }








# 3. Export token usage per PDF as CSV
@router.get("/usage/csv/{pdf_id}")
async def export_token_usage_csv(pdf_id: int, db: AsyncSession = Depends(get_db)):
    query = await db.execute(
        select(Token).where(Token.document_id == pdf_id)
    )
    token = query.scalar_one_or_none()
    if not token:
        raise HTTPException(status_code=404, detail="Token usage not found for this PDF")

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Document ID", "Total Tokens", "Chunk Count", "Cost", "Page-wise Tokens"])
    writer.writerow([
        token.document_id,
        token.total_token,
        token.chunk_count,
        token.cost,
        token.page_wise_token
    ])
    output.seek(0)

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=token_usage_{pdf_id}.csv"}
    )









@router.get("/api/admin/token/overview")
async def get_token_overview(request: Request, db: AsyncSession = Depends(get_db)):
    logger.info(f"Request received → {request.url.path}")

    # Query all documents with related tokens (only if token entries exist)
    stmt = (
        select(Document)
        .options(selectinload(Document.tokens))
        .join(Token, Token.document_id == Document.id)
        .group_by(Document.id)
    )
    result = await db.execute(stmt)
    documents = result.scalars().all()
    logger.info(f"Total documents fetched with tokens: {len(documents)}")

    response = []
    for doc in documents:
        token_count = len(doc.tokens)
        logger.info(f"Processing document id={doc.id}, filename={doc.filename}, tokens={token_count}")
        response.append({
            "id": doc.id,
            "filename": doc.filename,
            "uploaded_time": doc.uploaded_time,
            "version": doc.version,
            "doc_type": doc.doc_type,
            "status": doc.status.value if hasattr(doc.status, "value") else doc.status,
            "tokens": [
                {
                    "id": token.id,
                    "total_token": token.total_token,
                    "page_wise_token": token.page_wise_token,
                    "chunk_count": token.chunk_count,
                    "cost": token.cost
                }
                for token in doc.tokens
            ]
        })
        logger.info(f"Added document id={doc.id} to response")

    logger.info(f"Returning {len(response)} documents with token info for request {request.url.path}")
    return response


