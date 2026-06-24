from datetime import datetime
import os
import io
import fitz
import tempfile
from typing import Dict, List, Optional,Tuple
from aiohttp import Payload
from fastapi import APIRouter, Body, Path, Query, UploadFile, File, Request, Response, HTTPException, Depends, Form
from fastapi.responses import RedirectResponse
import fitz
from fastapi.responses import FileResponse, RedirectResponse
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from sqlalchemy import func, select, extract
from starlette.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.responses import StreamingResponse
from googleapiclient.http import MediaIoBaseDownload
from app.db.database import get_db
from datetime import datetime
from sqlalchemy.orm import selectinload
from googleapiclient.errors import HttpError
from app.schemas.schema import DocumentOut
from app.config.constant import CLIENT_SECRET_FILE,SCOPES,REDIRECT_URI,TOKEN_FILE,FOLDER_ID
from app.utils.logging_config import get_logger
from app.utils.util_file import (
    _scopes_list,
    extract_file_metadata,
    generate_pdf_thumbnail,
    get_all_uploaded_documents,
    get_or_create_drive_folder,
    store_document_metadata,
    to_ist,
    verify_pdf_has_content,
)
from app.utils.token_utils import load_credentials,save_creds,download_files_to_temp_dir
from app.db.models import Document, DriveFolder, Status, Token  # <-- DocType removed
from app.utils.validations import (
    is_valid_pdf,
    is_duplicate_in_drive,
    detect_pdf_type,
    get_pdf_page_count,
    validate_max_file_count,
    get_non_pdf_files, 
    get_encrypted_pdfs,
    validate_pdf_uploads,
)
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






router = APIRouter(prefix="/file", tags=["File Upload"])
logger = get_logger("file")



 
# -----------------------------------------------------------------------------
# OAuth (root callback to match your client JSON)
# -----------------------------------------------------------------------------

os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")


@router.get("/token/status")
def token_status():
    creds = load_credentials()
    if not creds:
        return {"exists": False}
    return {
        "exists": True,
        "expired": bool(creds.expired),
        "expiry": getattr(creds, "expiry", None).isoformat() if getattr(creds, "expiry", None) else None,
        "scopes": creds.scopes,
    }
 
@router.get("/")
def file_root():
    return {"detail": "Use /file/authorize to sign in, or open /ui for a simple interface."}
 
@router.get("/authorize")
def file_authorize():
    # IMPORTANT: use the global REDIRECT_URI = http://localhost:8000/oauth2callback
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRET_FILE,
        scopes=_scopes_list(),
        redirect_uri=REDIRECT_URI,
    )
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    return RedirectResponse(auth_url)
 
@router.get("/authorize-url")
def file_authorize_url():
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRET_FILE,
        scopes=_scopes_list(),
        redirect_uri=REDIRECT_URI,
    )
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    return {"auth_url": auth_url, "redirect_uri": REDIRECT_URI}
 
@router.get("/oauth2callback", name="oauth2callback")
def oauth2callback(request: Request):
    # Google will return ?code=... here (matches your client JSON)
    if "code" not in request.query_params:
        raise HTTPException(
            status_code=400,
            detail="Missing 'code'. Start at /file/authorize so Google redirects back here.",
        )
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRET_FILE,
        scopes=_scopes_list(),
        redirect_uri=REDIRECT_URI,
    )
    flow.fetch_token(authorization_response=str(request.url))
    save_creds(flow.credentials)
    return Response(" Authorized! You can close this tab and go back to /ui.")
 
# -----------------------------------------------------------------------------
# Drive helpers for brand/subfolder APIs
# -----------------------------------------------------------------------------





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

# def delete_drive_folder_recursive(service, folder_id: str):
#     """Recursively delete all files and subfolders inside a folder, then the folder itself."""
#     query = f"'{folder_id}' in parents and trashed = false"
#     results = service.files().list(q=query, fields="files(id, name, mimeType)").execute()
#     items = results.get("files", [])

#     for item in items:
#         try:
#             if item["mimeType"] == "application/vnd.google-apps.folder":
#                 # Recursively delete subfolder
#                 delete_drive_folder_recursive(service, item["id"])
#             else:
#                 # Delete file
#                 service.files().delete(fileId=item["id"]).execute()
#         except HttpError as e:
#             # Skip files without permission
#             print(f"Skipping {item['id']} due to {e}")

#     # Finally delete the folder itself
#     service.files().delete(fileId=folder_id).execute()


# async def _fetch_all_files_recursive(service, folder_id: str, folder_name: Optional[str] = None):
#     """Fetch folder tree recursively with all subfolders + files."""
#     all_items = await run_in_threadpool(_drive_list_children, service, folder_id, None, None)

#     folder_structure = {"id": folder_id, "name": folder_name, "subfolders": [], "files": []}

#     for item in all_items:
#         if item.get("mimeType") == FOLDER_MIME:
#             # Recursive call for subfolder
#             subfolder_data = await _fetch_all_files_recursive(service, item["id"], item["name"])
#             folder_structure["subfolders"].append(subfolder_data)
#         else:
#             folder_structure["files"].append({
#                 "id": item["id"],
#                 "name": item["name"],
#                 "mimeType": item.get("mimeType")
#             })

#     # If folder name is not provided (root call), assign default
#     if folder_structure["name"] is None:
#         folder_structure["name"] = "Root"

#     return folder_structure
 
# -----------------------------------------------------------------------------
# Brand/subfolder endpoints used by the UI
# -----------------------------------------------------------------------------
@router.post("/folder/create")
async def create_folder(
    request: Request,
    brand: str = Body(..., embed=True, description="Folder name to create"),
    parent_folder_id: Optional[str] = Body(None, embed=True, description="Google Drive parent folder id"),
    db: AsyncSession = Depends(get_db),
):
    logger.info(f" Endpoint: {request.url.path}  Request received: create_folder='{brand}', parent={parent_folder_id or 'ROOT'}")

    creds = load_credentials()
    if not creds or not creds.valid:
        logger.warning(f" Endpoint: {request.url.path}  Authorization failed while creating folder")
        raise HTTPException(
            status_code=401,
            detail="Authorization required. Click Authorize in /ui."
        )

    drive_service = build("drive", "v3", credentials=creds)
    parent_drive_id = parent_folder_id or FOLDER_ID
    logger.info(f" Endpoint: {request.url.path}  Using parent folder ID: {parent_drive_id}")

    query = f"name = '{brand}' and mimeType = 'application/vnd.google-apps.folder' and '{parent_drive_id}' in parents and trashed = false"
    response = drive_service.files().list(q=query, fields="files(id, name)").execute()
    files = response.get("files", [])

    if files:
        logger.warning(f" Endpoint: {request.url.path}  Folder '{brand}' already exists in Drive under parent {parent_drive_id}")
        raise HTTPException(
            status_code=400,
            detail=f"Folder '{brand}' already exists under this parent."
        )

    result = await db.execute(
        select(DriveFolder.id).where(DriveFolder.folder_id == parent_drive_id)
    )
    parent_db_id = result.scalar()
    logger.info(f" Endpoint: {request.url.path}  Parent DB ID: {parent_db_id}")

    result = await db.execute(
        select(DriveFolder).where(
            DriveFolder.name == brand,
            DriveFolder.parent_drive_id == parent_drive_id
        )
    )
    existing_folder = result.scalar_one_or_none()
    if existing_folder:
        logger.warning(f" Endpoint: {request.url.path}  Folder '{brand}' already exists in DB under parent {parent_drive_id}")
        raise HTTPException(
            status_code=400,
            detail=f"Folder '{brand}' already exists in DB under this parent."
        )

    file_metadata = {
        "name": brand,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_drive_id],
    }
    created = drive_service.files().create(body=file_metadata, fields="id, name, parents").execute()
    folder_id = created["id"]
    logger.info(f" Endpoint: {request.url.path}  Folder created in Drive: '{brand}', drive_id={folder_id}")

    folder = DriveFolder(
        folder_id=folder_id,
        parent_drive_id=parent_drive_id,
        name=brand,
        parent_id=parent_db_id
    )
    db.add(folder)
    await db.commit()
    await db.refresh(folder)
    logger.info(f" Endpoint: {request.url.path}  Folder saved in DB: '{brand}', db_id={folder.id}, drive_id={folder.folder_id}")

    return {
        "status": "success",
        "brand": folder.name,
        "folder_id": folder.folder_id,
        "parent_folder_id": folder.parent_drive_id,
        "parent_db_id": folder.parent_id,
        "db_id": folder.id,
    }












#----------------------------

#    all brands

#---------------------------

@router.get("/folders/root")
async def list_root_folders():
    creds = load_credentials()
    if not creds or not creds.valid:
        raise HTTPException(
            status_code=401,
            detail="Authorization required. Click Authorize in /ui."
        )

    drive_service = build("drive", "v3", credentials=creds)

    # Fetch direct children of root FOLDER_ID
    root_folders = await run_in_threadpool(
        _drive_list_children, drive_service, FOLDER_ID, FOLDER_MIME, None
    )

    folders = [{"id": f["id"], "name": f["name"]} for f in root_folders]

    return {
        "status": "success",
        "parentId": FOLDER_ID,   # root Google Drive folder
        "count": len(folders),   #  total number of root folders
        "folders": folders
    }


def normalize_status(doc: Document) -> str:
    """Map DB status to API response status"""
    status_val = str(getattr(doc.status, "value", doc.status)).lower()
    return "injected" if status_val == "injected" else "inprogress"

@router.post("/folders/subfolders")
async def list_folders(
    request: Request,
    folder_id: Optional[str] = Body(None, embed=True),
    file_flag: bool = Body(False, embed=True, description="If true, include files in the response"),
    search: Optional[str] = Query(None, description="Search keyword for files"),
    injected: bool = Query(False, description="If true, only show injected documents"),
    db: AsyncSession = Depends(get_db),
):
    logger.info(
        f"Endpoint: {request.url.path} Request received list_folders "
        f"(folder_id={folder_id}, file_flag={file_flag}, search={search}, injected={injected})"
    )

    creds = load_credentials()
    if not creds or not creds.valid:
        raise HTTPException(
            status_code=401,
            detail="Authorization required. Click Authorize in /ui."
        )

    drive_service = build("drive", "v3", credentials=creds)

    # ---- DB documents ----
    if injected:
        docs_result = await db.execute(
            select(Document).where(Document.status == Status.INJECTED)
        )
    else:
        docs_result = await db.execute(select(Document))

    docs = docs_result.scalars().all()
    db_file_map = {doc.file_id: doc for doc in docs if doc.file_id}
    db_file_ids = set(db_file_map.keys())

    # ---- DB folder IDs ----
    folder_result = await db.execute(select(DriveFolder.folder_id))
    db_folder_ids = {row[0] for row in folder_result.all() if row[0]}

    # ---- Search case ----
    if search:
        query = f"name contains '{search}'"
        if folder_id:
            query += f" and '{folder_id}' in parents"

        results = await run_in_threadpool(
            lambda: drive_service.files().list(
                q=query,
                fields="files(id, name, mimeType)"
            ).execute()
        )
        items = results.get("files", [])

        files = [
            {
                "id": f["id"],
                "name": f["name"],
                "mimeType": f.get("mimeType"),
                "status": normalize_status(db_file_map[f["id"]]) if f["id"] in db_file_map else None,
                "doc_type": db_file_map[f["id"]].doc_type if f["id"] in db_file_map else None,
                "uploaded_time": db_file_map[f["id"]].uploaded_time.isoformat()
                    if f["id"] in db_file_map and db_file_map[f["id"]].uploaded_time else None,
                "injected_time": db_file_map[f["id"]].injected_time.isoformat()
                    if f["id"] in db_file_map and db_file_map[f["id"]].injected_time else None,
                "thumbnail_path": db_file_map[f["id"]].thumbnail_path if f["id"] in db_file_map else None,
            }
            for f in items   # corrected from all_items
            if (
                f.get("mimeType") != FOLDER_MIME
                and f["id"] in db_file_ids
                and (not injected or str(getattr(db_file_map[f["id"]].status, "value", db_file_map[f["id"]])).lower() == "injected")
            )
        ]

        folders = [
            {"id": f["id"], "name": f["name"]}
            for f in items
            if f.get("mimeType") == FOLDER_MIME and f["id"] in db_folder_ids
        ]

        return {
            "status": "success",
            "parentId": folder_id or "",
            "folders": folders,
            "files": files,
        }

    # ---- Non-search logic ----
    parent_drive_id = folder_id if folder_id else FOLDER_ID
    subfolders = await run_in_threadpool(
        _drive_list_children,
        drive_service,
        parent_drive_id,
        FOLDER_MIME,
        None
    )
    subfolders = [f for f in subfolders if f["id"] in db_folder_ids]

    if file_flag and folder_id:
        all_items = await run_in_threadpool(
            _drive_list_children,
            drive_service,
            parent_drive_id,
            None,
            None
        )

        files = [
            {
                "id": f["id"],
                "name": f["name"],
                "mimeType": f.get("mimeType"),
                "status": normalize_status(db_file_map[f["id"]]) if f["id"] in db_file_map else None,  
                "doc_type": db_file_map[f["id"]].doc_type if f["id"] in db_file_map else None,
                "uploaded_time": db_file_map[f["id"]].uploaded_time.isoformat()
                    if f["id"] in db_file_map and db_file_map[f["id"]].uploaded_time else None,
                "injected_time": db_file_map[f["id"]].injected_time.isoformat()
                    if f["id"] in db_file_map and db_file_map[f["id"]].injected_time else None,
                "thumbnail_path": db_file_map[f["id"]].thumbnail_path if f["id"] in db_file_map else None,
            }
            for f in all_items
            if f.get("mimeType") != FOLDER_MIME and f["id"] in db_file_ids
        ]

        return {
            "status": "success",
            "parentId": folder_id,
            "folders": [{"id": f["id"], "name": f["name"]} for f in subfolders],
            "files": files,
        }

    return {
        "status": "success",
        "parentId": folder_id or None,
        "folders": [{"id": f["id"], "name": f["name"]} for f in subfolders],
    }













@router.post("/folder/rename")
async def rename_folder(
    request: Request,
    folder_id: str = Body(..., embed=True),
    new_name: str = Body(..., embed=True),
):
    logger.info(f"Endpoint: {request.url.path} Request received rename_folder (folder_id={folder_id}, new_name='{new_name}')")

    creds = load_credentials()
    if not creds or not creds.valid:
        logger.warning(f"Endpoint: {request.url.path} Authorization failed in rename_folder")
        raise HTTPException(
            status_code=401,
            detail="Authorization required. Click Authorize in /ui."
        )
    logger.info(f"Endpoint: {request.url.path} Authorization successful")

    drive_service = build("drive", "v3", credentials=creds)

    try:
        logger.info(f"Endpoint: {request.url.path} Renaming folder_id={folder_id} to '{new_name}'")
        updated = drive_service.files().update(
            fileId=folder_id,
            body={"name": new_name},
            fields="id, name"
        ).execute()
        logger.info(f"Endpoint: {request.url.path} Folder renamed successfully (id={updated['id']}, new_name='{updated['name']}')")

        return {"status": "success", "id": updated["id"], "new_name": updated["name"]}
    except Exception as e:
        logger.exception(f"Endpoint: {request.url.path} Failed to rename folder_id={folder_id} to '{new_name}'")
        raise HTTPException(status_code=500, detail=str(e))




@router.delete("/folder/delete")
async def delete_folder(
    request: Request,
    folder_id: str = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
):
    logger.info(f"Endpoint: {request.url.path} Request received delete_folder (folder_id={folder_id})")

    creds = load_credentials()
    if not creds or not creds.valid:
        logger.warning(f"Endpoint: {request.url.path} Authorization failed in delete_folder")
        raise HTTPException(
            status_code=401,
            detail="Authorization required. Click Authorize in /ui."
        )
    logger.info(f"Endpoint: {request.url.path} Authorization successful")

    drive_service = build("drive", "v3", credentials=creds)

    # Step 1: Lookup folder in DB
    logger.info(f"Endpoint: {request.url.path} Looking up folder in DB for folder_id={folder_id}")
    result = await db.execute(select(DriveFolder).filter(DriveFolder.folder_id == folder_id))
    folder = result.scalars().first()

    if not folder:
        logger.warning(f"Endpoint: {request.url.path} Folder not found in DB (folder_id={folder_id})")
        raise HTTPException(status_code=404, detail="Folder not found in database")

    try:
        # Step 2: Delete folder from Google Drive
        logger.info(f"Endpoint: {request.url.path} Deleting folder from Google Drive (folder_id={folder_id})")
        drive_service.files().delete(fileId=folder_id).execute()
        logger.info(f"Endpoint: {request.url.path} Folder deleted from Google Drive (folder_id={folder_id})")

    except HttpError as e:
        if e.resp.status == 404:
            logger.warning(f"Endpoint: {request.url.path} Folder not found in Google Drive, proceeding with DB cleanup (folder_id={folder_id})")
            pass
        else:
            logger.exception(f"Endpoint: {request.url.path} Google API error while deleting folder_id={folder_id}")
            raise HTTPException(status_code=500, detail=f"Google API error: {str(e)}")

    # Step 3: Delete folder + documents from DB
    try:
        logger.info(f"Endpoint: {request.url.path} Deleting folder and related documents from DB (folder_id={folder_id}, name='{folder.name}')")
        await db.delete(folder)   # cascades to documents because of relationship
        await db.commit()
        logger.info(f"Endpoint: {request.url.path} Folder and related documents deleted from DB (folder_id={folder_id})")
    except Exception as e:
        await db.rollback()
        logger.exception(f"Endpoint: {request.url.path} Database error while deleting folder_id={folder_id}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

    return {"status": "success", "message": f"Folder '{folder.name}' deleted from Drive & DB"}








@router.get("/folders/contents")
async def get_folder_contents(
    request: Request,
    folder_id: str = Query(..., description="Google Drive folder ID")
):
    logger.info(f"Endpoint: {request.url.path} Request received get_folder_contents (folder_id={folder_id})")

    creds = load_credentials()
    if not creds or not creds.valid:
        logger.warning(f"Endpoint: {request.url.path} Authorization failed in get_folder_contents")
        raise HTTPException(
            status_code=401,
            detail="Authorization required. Click Authorize in /ui."
        )
    logger.info(f"Endpoint: {request.url.path} Authorization successful")

    drive_service = build("drive", "v3", credentials=creds)

    # --- Get subfolders ---
    logger.info(f"Endpoint: {request.url.path} Fetching subfolders for folder_id={folder_id}")
    subfolders = await run_in_threadpool(
        _drive_list_children, drive_service, folder_id, FOLDER_MIME, None
    )
    logger.info(f"Endpoint: {request.url.path} Retrieved {len(subfolders)} subfolders for folder_id={folder_id}")

    # --- Get files (exclude folders) ---
    logger.info(f"Endpoint: {request.url.path} Fetching files for folder_id={folder_id}")
    all_items = await run_in_threadpool(
        _drive_list_children, drive_service, folder_id, None, None
    )
    files = [f for f in all_items if f.get("mimeType") != FOLDER_MIME]
    logger.info(f"Endpoint: {request.url.path} Retrieved {len(files)} files for folder_id={folder_id}")

    return {
        "status": "success",
        "parentId": folder_id,
        "subfolders": [
            {"id": f["id"], "name": f["name"]}
            for f in subfolders
        ],
        "files": [
            {"id": f["id"], "name": f["name"], "mimeType": f.get("mimeType")}
            for f in files
        ],
    }








# ----------------------
# Upload File to Drive
# ----------------------

@router.post("/upload_file")
async def upload_brand_files(
    request: Request,
    brand_id: Optional[str] = Form(None, description="Target Google Drive folder ID"),
    brand: Optional[str] = Form(None, description="Brand folder name (used if brand_id not provided)"),
    subpath: Optional[str] = Form(None, description="Optional nested subpath like 'Q3/Invoices'"),
    files: List[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
):
    logger.info(f"Endpoint: {request.url.path} Request received: Upload {len(files)} files, brand='{brand}', folder={brand_id or 'ROOT'}")

    creds = load_credentials()
    if not creds or not creds.valid:
        logger.warning(f"Endpoint: {request.url.path} Authorization failed during upload")
        raise HTTPException(status_code=401, detail="Authorization required.")
    logger.info(f"Endpoint: {request.url.path} Authorization successful")
    drive_service = build("drive", "v3", credentials=creds)

    validate_pdf_uploads(files)
    logger.info(f"Endpoint: {request.url.path} PDF validation passed")

    # Resolve target folder
    if brand_id:
        parent_id = brand_id
        normalized_subpath = None
        logger.info(f"Endpoint: {request.url.path} Using provided brand_id={brand_id} as target folder")
    else:
        if not brand:
            logger.error(f"Endpoint: {request.url.path} Neither brand_id nor brand provided")
            raise HTTPException(status_code=400, detail="Either brand_id or brand is required.")
        logger.info(f"Endpoint: {request.url.path} Resolving Drive path for brand='{brand}', subpath='{subpath}'")
        parent_id, normalized_subpath = await run_in_threadpool(
            ensure_drive_path, drive_service, FOLDER_ID, brand, subpath
        )
        logger.info(f"Endpoint: {request.url.path} Resolved target folder  {parent_id}, normalized_subpath='{normalized_subpath}'")

    # Ensure DriveFolder row in DB
    folder_obj = await get_or_create_drive_folder(
        db=db,
        drive_folder_id=parent_id,
        name=normalized_subpath or brand or "Root",
        parent_drive_id=FOLDER_ID
    )
    logger.info(f"Endpoint: {request.url.path} DB folder ensured  ID={folder_obj.id}, Name='{folder_obj.name}'")

    results = []
    verified_changes = []

    async def upload_to_drive(file: UploadFile):
        logger.info(f"Endpoint: {request.url.path} Starting upload for '{file.filename}'")

        existing_doc = await db.execute(
            select(Document).where(Document.filename == file.filename)
        )
        if existing_doc.scalar_one_or_none():
            logger.error(f"Endpoint: {request.url.path} Duplicate file detected: '{file.filename}'")
            raise HTTPException(
                status_code=409,
                detail=f"'{file.filename}' already exists in drive (cannot upload again)."
            )

        file_data = await file.read()
        logger.info(f"Endpoint: {request.url.path} Read file data → {len(file_data)} bytes for '{file.filename}'")
        file.file.seek(0)
        stream = io.BytesIO(file_data)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(file_data)
            tmp.flush()
            tmp_path = tmp.name
        logger.info(f"Endpoint: {request.url.path} Temporary file created at {tmp_path}")

        metadata = extract_file_metadata(tmp_path)
        doc_type = detect_pdf_type(file_data)
        metadata["detected_doc_type"] = doc_type
        logger.info(f"Endpoint: {request.url.path} Extracted metadata for '{file.filename}', doc_type={doc_type}")

        is_ok, reason = verify_pdf_has_content(file_data)
        verified = bool(is_ok)
        logger.info(f"Endpoint: {request.url.path} Verification for '{file.filename}': {verified} ({reason if not verified else 'OK'})")

        thumbnail_path = await run_in_threadpool(generate_pdf_thumbnail, file_data, file.filename)
        metadata["thumbnail_path"] = thumbnail_path
        logger.info(f"Endpoint: {request.url.path} Generated thumbnail for '{file.filename}' at {thumbnail_path}")

        media = MediaIoBaseUpload(stream, mimetype=file.content_type, resumable=True)
        file_metadata = {"name": file.filename, "parents": [parent_id]}

        def _execute_upload():
            return drive_service.files().create(
                body=file_metadata, media_body=media, fields="id, name"
            ).execute()

        uploaded = await run_in_threadpool(_execute_upload)
        logger.info(f"Endpoint: {request.url.path} Uploaded to Google Drive  id={uploaded['id']}, name='{uploaded['name']}'")

        metadata["file_id"] = uploaded["id"]
        final_status = Status.VERIFIED if verified else Status.UPLOADED

        db_doc = await store_document_metadata(
            db=db,
            filename=file.filename,
            metadata=metadata,
            doc_type=doc_type,
            status=final_status,
            file_id=uploaded["id"],
        )

        db_doc.folder_id = folder_obj.id
        db_doc.thumbnail_path = thumbnail_path
        await db.commit()
        await db.refresh(db_doc)
        logger.info(f"Endpoint: {request.url.path} DB metadata stored for '{file.filename}' (DB_ID={db_doc.id})")

        result = {
            "file_id": uploaded["id"],
            "db_id": db_doc.id,
            "name": uploaded["name"],
            "brand_folder": brand or folder_obj.name,
            "subfolder": normalized_subpath or "",
            "folder_id": folder_obj.id,
            "verified": verified,
            "thumbnail_path": thumbnail_path,
        }
        if not verified:
            result["verification_reason"] = reason
        if verified:
            verified_changes.append({
                "file_id": uploaded["id"],
                "db_id": db_doc.id,
                "name": uploaded["name"],
                "status": "VERIFIED"
            })

        logger.info(f"Endpoint: {request.url.path} Completed processing for '{file.filename}'")
        return result

    for file in files:
        logger.info(f"Endpoint: {request.url.path} Processing file '{file.filename}'")
        results.append(await upload_to_drive(file))

    logger.info(f"Endpoint: {request.url.path} Upload completed: {len(results)} files processed, {len(verified_changes)} verified")

    return {
        "status": "success",
        "brand": brand,
        "subpath": normalized_subpath or "",
        "folder_id": folder_obj.id,
        "files": results,
        "verified_changes": verified_changes
    }






# delete files 


@router.delete("/delete_file/{file_id}")
async def delete_file(
    request: Request,
    file_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Delete a file from Google Drive and DB using file_id.
    """
    logger.info(f"Endpoint: {request.url.path} Request received (file_id={file_id})")

    # Load Google Drive credentials
    creds = load_credentials()
    if not creds or not creds.valid:
        logger.warning(f"Endpoint: {request.url.path} Authorization failed")
        raise HTTPException(status_code=401, detail="Authorization required.")
    logger.info(f"Endpoint: {request.url.path} Authorization successful")

    drive_service = build("drive", "v3", credentials=creds)

    # Find document in DB
    from sqlalchemy import select
    logger.info(f"Endpoint: {request.url.path} Looking up file_id={file_id} in DB")
    result = await db.execute(select(Document).where(Document.file_id == file_id))
    db_doc = result.scalar_one_or_none()
    if not db_doc:
        logger.error(f"Endpoint: {request.url.path} File not found in DB (file_id={file_id})")
        raise HTTPException(status_code=404, detail="File not found in DB")
    logger.info(f"Endpoint: {request.url.path} File found in DB → id={db_doc.id}, name={db_doc.filename}")

    # Delete from Google Drive
    try:
        logger.info(f"Endpoint: {request.url.path} Deleting file from Google Drive (file_id={file_id})")

        def _execute_delete():
            return drive_service.files().delete(fileId=file_id).execute()

        await run_in_threadpool(_execute_delete)
        logger.info(f"Endpoint: {request.url.path} File deleted from Google Drive (file_id={file_id})")
    except Exception as e:
        logger.error(f"Endpoint: {request.url.path} Google Drive delete failed (file_id={file_id}) → {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to delete from Google Drive: {str(e)}")

    # Delete from DB
    try:
        logger.info(f"Endpoint: {request.url.path} Deleting file from DB (db_id={db_doc.id}, file_id={file_id})")
        await db.delete(db_doc)
        await db.commit()
        logger.info(f"Endpoint: {request.url.path} File deleted from DB (db_id={db_doc.id}, file_id={file_id})")
    except Exception as e:
        await db.rollback()
        logger.error(f"Endpoint: {request.url.path} DB delete failed (file_id={file_id}) → {str(e)}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

    return {
        "status": "success",
        "message": f"File {db_doc.filename} deleted successfully",
        "deleted_file_id": file_id,
        "db_id": db_doc.id
    }












# ----------------------
# List all uploaded PDFs
# ----------------------


@router.get("/file/list")
async def list_uploaded_pdfs(
    request: Request,
    db: AsyncSession = Depends(get_db),
    search: str = Query(None, description="Search by filename"),
    filter_date: str = Query(None, description="Filter by upload date (YYYY-MM-DD)"),
    sort_order: str = Query("desc", regex="^(asc|desc)$", description="Sort by uploaded date: asc or desc"),
    page: int = Query(0, ge=0, description="Page number"),
    limit: int = Query(10, ge=1, le=1000, description="Items per page")
):
    logger.info(f"Endpoint: {request.url.path} Request received (search={search}, filter_date={filter_date}, sort_order={sort_order}, page={page}, limit={limit})")

    try:
        # Base query
        query = select(Document).options(selectinload(Document.tokens))

        # Search filter
        if search:
            logger.info(f"Endpoint: {request.url.path} Applying search filter: {search}")
            query = query.where(Document.filename.ilike(f"%{search}%"))

        # Date filter
        if filter_date:
            try:
                date_obj = datetime.strptime(filter_date, "%Y-%m-%d").date()
                logger.info(f"Endpoint: {request.url.path} Applying date filter: {filter_date}")
                query = query.where(func.date(Document.uploaded_time) == date_obj)
            except ValueError:
                logger.error(f"Endpoint: {request.url.path} Invalid date format provided: {filter_date}")
                return {"status": "error", "message": "Invalid date format. Use YYYY-MM-DD"}

        # Sorting
        logger.info(f"Endpoint: {request.url.path} Sorting order: {sort_order}")
        if sort_order == "asc":
            query = query.order_by(Document.uploaded_time.asc())
        else:
            query = query.order_by(Document.uploaded_time.desc())

        # Find latest uploaded_time in DB (not paginated)
        latest_uploaded_time = (
            await db.execute(select(func.max(Document.uploaded_time)))
        ).scalar()
        logger.info(f"Endpoint: {request.url.path} Latest uploaded_time in DB: {latest_uploaded_time}")

        # Count total
        total_count_query = select(func.count()).select_from(query.subquery())
        total_count = (await db.execute(total_count_query)).scalar()
        logger.info(f"Endpoint: {request.url.path} Total matching documents: {total_count}")

        # Pagination
        offset = page * limit
        query = query.offset(offset).limit(limit)
        logger.info(f"Endpoint: {request.url.path} Pagination applied  offset={offset}, limit={limit}")

        # Fetch
        result = await db.execute(query)
        documents = result.scalars().all()
        logger.info(f"Endpoint: {request.url.path} Fetched {len(documents)} documents for this page")

        return {
            "status": "success",
            "page": page,
            "limit": limit,
            "total": total_count,
            "files": [
                {
                    "filename": doc.filename,
                    "doc_id": doc.id,
                    "doc_type": doc.doc_type,
                    "uploaded_time": to_ist(doc.injected_time),
                    "status": doc.status,
                    "version": doc.version,
                    "meta_data": doc.meta_data,
                    "new": (doc.uploaded_time == latest_uploaded_time),
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
                }
                for doc in documents
            ]
        }

    except Exception as e:
        logger.error(f"Endpoint: {request.url.path} Error  {str(e)}")
        return {"status": "error", "message": str(e)}

    





# ----------------------
# Document Details
# ----------------------



@router.get("/document/{doc_id}/details")
async def get_document_stats(request: Request, doc_id: str, db: AsyncSession = Depends(get_db)):
    logger.info(f"Endpoint: {request.url.path} Request received for doc_id={doc_id}")

    # Fetch document using file_id
    result = await db.execute(select(Document).where(Document.file_id == doc_id))
    document = result.scalar_one_or_none()

    if not document:
        logger.warning(f"Endpoint: {request.url.path} Document not found in DB for doc_id={doc_id}")
        raise HTTPException(status_code=404, detail="Document not found")

    logger.info(f"Endpoint: {request.url.path} Document found filename={document.filename}, id={document.id}")

    # Fetch token stats (use document.id, not file_id)
    token_result = await db.execute(select(Token).where(Token.document_id == document.id))
    tokens = token_result.scalars().all()
    total_chunk_count = sum(t.chunk_count for t in tokens) if tokens else 0
    logger.info(f"Endpoint: {request.url.path} Total chunks for document '{document.filename}': {total_chunk_count}")

    # Extract metadata info safely
    metadata = document.meta_data or {}
    file_size = metadata.get("file_size_mb")
    total_pages = metadata.get("page_count")
    logger.info(f"Endpoint: {request.url.path} Metadata file_size_mb={file_size}, total_pages={total_pages}")

    return {
        "doc_id": doc_id,
        "filename": document.filename,
        "file_size_mb": file_size,
        "total_pages": total_pages,
        "total_chunk_count": total_chunk_count,
    }



 






# doc with status





@router.get("/doc_with_status")
async def get_documents_by_status(request: Request, db: AsyncSession = Depends(get_db)) -> Dict[str, List[dict]]:
    logger.info(f"Endpoint: {request.url.path} Request received")

    result = await db.execute(select(Document))
    docs = result.scalars().all()
    logger.info(f"Endpoint: {request.url.path} Total documents fetched from DB: {len(docs)}")

    verified_docs = [
        {
            "id": d.id,
            "filename": d.filename,
            "file_id": d.file_id,
            "uploaded_time": d.uploaded_time,
            "folder_id": d.folder_id
        }
        for d in docs if d.status == Status.VERIFIED
    ]
    logger.info(f"Endpoint: {request.url.path} Documents with VERIFIED status: {len(verified_docs)}")

    injected_docs = [
        {
            "id": d.id,
            "filename": d.filename,
            "file_id": d.file_id,
            "injected_time": d.injected_time,
            "folder_id": d.folder_id
        }
        for d in docs if d.status == Status.INJECTED
    ]
    logger.info(f"Endpoint: {request.url.path} Documents with INJECTED status: {len(injected_docs)}")

    return {
        "not_injected": verified_docs,
        "injected": injected_docs,
    }








# ----------------------
# Document view
# ----------------------


@router.get("/view_files/{file_id}")
async def get_pdf_file(request: Request, file_id: str):
    logger.info(f"Endpoint: {request.url.path} Request received for file_id: {file_id}")

    creds = load_credentials()
    if not creds or not creds.valid:
        logger.warning(f"Endpoint: {request.url.path} Authorization failed for file_id: {file_id}")
        raise HTTPException(status_code=401, detail="Authorization required.")

    drive_service = build("drive", "v3", credentials=creds)

    # Get the file name from Google Drive
    file_meta = drive_service.files().get(fileId=file_id, fields="name, mimeType").execute()
    logger.info(f"Endpoint: {request.url.path} Fetched file metadata: name={file_meta.get('name')}, mimeType={file_meta.get('mimeType')}")

    if file_meta["mimeType"] != "application/pdf":
        logger.warning(f"Endpoint: {request.url.path} File is not a PDF: {file_meta.get('name')} ({file_id})")
        raise HTTPException(status_code=400, detail="File is not a PDF.")

    # Download from Google Drive into memory
    request_media = drive_service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request_media)
    done = False
    while not done:
        status, done = downloader.next_chunk()
        if status:
            logger.info(f"Endpoint: {request.url.path} Download progress: {int(status.progress() * 100)}%")

    fh.seek(0)
    logger.info(f"Endpoint: {request.url.path} File download completed: {file_meta.get('name')}")

    # Return as a stream; 'inline' makes it viewable if browser supports PDF
    return StreamingResponse(
        fh,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{file_meta["name"]}"'
        }
    )



# ----------------------
# Update status endpoint
# ----------------------
@router.patch("/file/update-status/{doc_id}")
async def update_document_status(
    doc_id: int = Path(..., description="ID of the document to update"),
    new_status: Status = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()

    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    doc.status = new_status
    await db.commit()
    await db.refresh(doc)

    return {
        "status": "success",
        "message": f"Document ID {doc_id} status updated to {new_status.value}",
        "document": {
            "id": doc.id,
            "filename": doc.filename,
            "status": doc.status.value,
            "uploaded_time": doc.uploaded_time.isoformat(),
        },
    }


# automatic verification of file status


@router.post("/verify_file/{file_id}")
async def verify_pdf_file(request: Request, file_id: str, db: AsyncSession = Depends(get_db)):
    logger.info(f"Endpoint: {request.url.path} Request received for file_id: {file_id}")

    # 1. Load Google Drive credentials
    creds = load_credentials()
    if not creds or not creds.valid:
        logger.warning(f"Endpoint: {request.url.path} Authorization failed for file_id: {file_id}")
        raise HTTPException(status_code=401, detail="Authorization required.")

    # 2. Download file from Google Drive
    drive_service = build("drive", "v3", credentials=creds)
    file_meta = drive_service.files().get(fileId=file_id, fields="name, mimeType").execute()
    logger.info(f"Endpoint: {request.url.path} Fetched file metadata: name={file_meta.get('name')}, mimeType={file_meta.get('mimeType')}")

    if file_meta["mimeType"] != "application/pdf":
        logger.warning(f"Endpoint: {request.url.path} File is not a PDF: {file_meta.get('name')} ({file_id})")
        raise HTTPException(status_code=400, detail="File is not a PDF.")

    request_media = drive_service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request_media)
    done = False
    while not done:
        status, done = downloader.next_chunk()
        if status:
            logger.info(f"Endpoint: {request.url.path} Download progress: {int(status.progress() * 100)}%")

    fh.seek(0)
    logger.info(f"Endpoint: {request.url.path} File download completed: {file_meta.get('name')}")

    # 3. Validate with PyMuPDF
    try:
        doc = fitz.open(stream=fh, filetype="pdf")
        if len(doc) == 0:
            logger.warning(f"Endpoint: {request.url.path} PDF has no pages: {file_meta.get('name')}")
            raise HTTPException(status_code=400, detail="PDF has no pages.")

        has_content = False
        for page in doc:
            text = page.get_text().strip()
            images = page.get_images(full=True)
            if text or images:
                has_content = True
                break

        doc.close()

        if not has_content:
            logger.warning(f"Endpoint: {request.url.path} No text or image content found in PDF: {file_meta.get('name')}")
            raise HTTPException(status_code=400, detail="No text or image content found in PDF.")

    except Exception as e:
        logger.error(f"Endpoint: {request.url.path} PDF validation failed for {file_meta.get('name')}: {str(e)}")
        raise HTTPException(status_code=400, detail=f"PDF validation failed: {str(e)}")

    # 4. Update document status in DB
    result = await db.execute(
        select(Document).where(Document.file_id == file_id)
    )
    document = result.scalar_one_or_none()
    if not document:
        logger.warning(f"Endpoint: {request.url.path} Document not found in DB for file_id: {file_id}")
        raise HTTPException(status_code=404, detail="Document not found in database.")

    document.status = Status.VERIFIED
    await db.commit()
    logger.info(f"Endpoint: {request.url.path} Document status updated to VERIFIED for file_id: {file_id}")

    return {"message": "PDF successfully verified and status updated to 'verified'."}






# ----------------------
# Download batch of files
# ----------------------
@router.post("/file/download-batch")
async def download_batch(file_ids: List[int], db: AsyncSession = Depends(get_db)):
    local_paths = await download_files_to_temp_dir(db, ids=file_ids)
    return {"downloaded_files": local_paths}


# ---------------------------------
# Upload modified PDF (new version)
# ---------------------------------
@router.post("/upload-modify/pdf")
async def upload_modify_pdf(
    doc_id: int = Form(...),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    # 1. Find existing document
    result = await db.execute(select(Document).where(Document.id == doc_id))
    existing_doc = result.scalar_one_or_none()
    if not existing_doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # 2. Create new version filename
    base_name, ext = os.path.splitext(existing_doc.filename)
    original_filename = existing_doc.filename
    new_version = existing_doc.version + 1
    new_filename = f"{base_name}({new_version}).pdf"

    # 3. Load credentials
    creds = load_credentials()
    if not creds or not creds.valid:
        raise HTTPException(status_code=401, detail="Authorization required. Visit /file/authorize first.")

    drive_service = build("drive", "v3", credentials=creds)

    # 4. Validate PDF
    file_data = await file.read()

    # --- ADD THESE VALIDATIONS ---

    #  Ensure it's a PDF file
    ext = os.path.splitext(file.filename)[1].lower()
    if ext != ".pdf":
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Only PDF files are allowed.",
                "invalid_file": {"filename": file.filename, "format": ext.lstrip(".")}
            }
        )

    #  Check if PDF is encrypted
    try:
        with fitz.open(stream=file_data, filetype="pdf") as doc:
            if doc.is_encrypted:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "message": "Encrypted PDF detected. Please upload only non-password-protected files.",
                        "encrypted_file": file.filename
                    }
                )
    except Exception:
        raise HTTPException(
            status_code=400,
            detail=f"'{file.filename}' could not be read as a valid PDF."
        )


    #  File size validation
    MAX_FILE_SIZE_MB = 25
    if len(file_data) > MAX_FILE_SIZE_MB * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail=f"'{file.filename}' exceeds maximum allowed size of {MAX_FILE_SIZE_MB} MB."
        )

    if not is_valid_pdf(file_data):
        raise HTTPException(status_code=400, detail=f"'{file.filename}' is not a valid PDF.")
    
    #  Page count validation
    page_count = get_pdf_page_count(file_data)
    MAX_PAGE_COUNT = 10
    if page_count > MAX_PAGE_COUNT:
        raise HTTPException(
            status_code=400,
            detail=f"'{file.filename}' has {page_count} pages which exceeds the allowed limit of {MAX_PAGE_COUNT} pages."
        )

    # 5. Extract metadata & doc_type (string)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(file_data)
        tmp.flush()
        tmp_path = tmp.name

    metadata = extract_file_metadata(tmp_path)
    doc_type = detect_pdf_type(file_data)
    metadata["detected_doc_type"] = doc_type

    # 6. Upload to Drive
    stream = io.BytesIO(file_data)
    media = MediaIoBaseUpload(stream, mimetype=file.content_type, resumable=True)
    file_metadata = {"name": new_filename, "parents": [FOLDER_ID]}

    def _execute_upload():
        return drive_service.files().create(
            body=file_metadata, media_body=media, fields="id, name"
        ).execute()

    uploaded = await run_in_threadpool(_execute_upload)

    # 7. Save in DB (increment version)
    metadata["file_id"] = uploaded["id"]
    db_doc = await store_document_metadata(
        db=db,
        filename=new_filename,
        metadata=metadata,
        doc_type=doc_type,
        status=Status.UPLOADED,
        version=new_version,
        file_id=uploaded["id"],
    )

    return {
        "status": "success",
        "message": f"modified file uploaded as {new_filename}",
        "original_file": original_filename,
        "new_file": new_filename,
        "version": new_version,
        "db_id": db_doc.id
    }

# =================================================
    #   documents/monthly
# =================================================
    

@router.get("/documents/monthly")
async def get_monthly_documents(
    month: int = Query(..., ge=1, le=12, description="Month number (1-12)"),
    year: int = Query(datetime.now().year, ge=2000, le=2100, description="Year (default: current year)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns documents uploaded in a specific month and year with:
    - Total document count
    - Document IDs
    - Full document details
    """
    try:
        # Filter documents by month and year
        query = select(Document).where(
            extract('month', Document.uploaded_time) == month,
            extract('year', Document.uploaded_time) == year
        ).options(selectinload(Document.tokens))

        result = await db.execute(query)
        documents = result.scalars().all()

        total_count = len(documents)
        doc_ids = [doc.id for doc in documents]

        return {
            "status": "success",
            "month": month,
            "year": year,
            "total_count": total_count,
            "document_ids": doc_ids,
            "documents": [
                {
                    "id": doc.id,
                    "filename": doc.filename,
                    "doc_type": doc.doc_type,
                    "uploaded_time": doc.uploaded_time,
                    "status": doc.status,
                    "version": doc.version,
                    "meta_data": doc.meta_data,
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
                }
                for doc in documents
            ]
        }

    except Exception as e:
        return {"status": "error", "message": str(e)}



THUMBNAIL_DIR = "thumbnails"  # same as in your code

@router.get("/thumbnails/{filename}")
async def serve_thumbnail(filename: str):
    file_path = os.path.join(THUMBNAIL_DIR, filename)

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(file_path)