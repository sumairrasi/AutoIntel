import os
from typing import Optional,Tuple
from starlette.concurrency import run_in_threadpool
from fastapi import HTTPException
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from app.config.constant import SCOPES, TOKEN_FILE, SCOPES

FOLDER_MIME = "application/vnd.google-apps.folder"


def _scopes_list():
    return SCOPES if isinstance(SCOPES, (list, tuple)) else str(SCOPES).split()

def save_creds(creds: Credentials) -> None:
    with open(TOKEN_FILE, "w") as f:
        f.write(creds.to_json())



def load_credentials() -> Optional[Credentials]:
    token_path = os.getenv("TOKEN_FILE", "/run/credentials/token.json")
    print(f"[DEBUG] Using TOKEN_FILE={token_path}")   
    if not os.path.isfile(token_path):
        raise FileNotFoundError(f"TOKEN_FILE not found: {token_path}")

    try:
        creds = Credentials.from_authorized_user_file(token_path, scopes=_scopes_list())
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(GoogleRequest())
            save_creds(creds)
        return creds
    except Exception as e:
        raise RuntimeError(f"Failed to load credentials from {token_path}: {e}")












 
def _drive_list_children(drive_service, parent_id: str, mime_filter: Optional[str] = None, name_eq: Optional[str] = None):
    q_parts = [f"'{parent_id}' in parents", "trashed=false"]
    if mime_filter:
        q_parts.append(f"mimeType='{mime_filter}'")
    if name_eq:
        safe_name = name_eq.replace("'", "\\'")
        q_parts.append(f"name='{safe_name}'")
    q = " and ".join(q_parts)
 
    results = []
    page_token = None
    while True:
        resp = drive_service.files().list(
            q=q,
            spaces="drive",
            fields="nextPageToken, files(id, name, mimeType)",
            pageToken=page_token,
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
        ).execute()
        results.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return results
 
def _drive_find_child_by_name(drive_service, parent_id: str, name: str, require_folder: bool = False) -> Optional[dict]:
    mime = FOLDER_MIME if require_folder else None
    items = _drive_list_children(drive_service, parent_id, mime_filter=mime, name_eq=name)
    return items[0] if items else None
 
def _drive_create_folder(drive_service, name: str, parent_id: str) -> dict:
    body = {"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]}
    return drive_service.files().create(
        body=body, fields="id, name, mimeType", supportsAllDrives=True
    ).execute()
 
def ensure_drive_path(drive_service, root_folder_id: str, brand: str, subpath: Optional[str]) -> Tuple[str, str]:
    if not brand or not brand.strip():
        raise HTTPException(status_code=400, detail="Brand folder name is required.")
    brand = brand.strip()
    if not root_folder_id:
        raise HTTPException(status_code=500, detail="FOLDER_ID is not configured on server.")
 
    brand_node = _drive_find_child_by_name(drive_service, root_folder_id, brand, require_folder=True)
    if not brand_node:
        brand_node = _drive_create_folder(drive_service, brand, root_folder_id)
 
    parent_id = brand_node["id"]
    normalized = ""
    if subpath:
        parts = [p.strip() for p in subpath.split("/") if p.strip()]
        for part in parts:
            existing = _drive_find_child_by_name(drive_service, parent_id, part, require_folder=True)
            if not existing:
                existing = _drive_create_folder(drive_service, part, parent_id)
            parent_id = existing["id"]
        normalized = "/".join(parts)
    return parent_id, normalized
 
def is_duplicate_in_parent(drive_service, parent_id: str, filename: str) -> bool:
    items = _drive_list_children(drive_service, parent_id, mime_filter=None, name_eq=filename)
    return any(item.get("mimeType") != FOLDER_MIME for item in items)

def delete_drive_folder_recursive(service, folder_id: str):
    """Recursively delete all files and subfolders inside a folder, then the folder itself."""
    query = f"'{folder_id}' in parents and trashed = false"
    results = service.files().list(q=query, fields="files(id, name, mimeType)").execute()
    items = results.get("files", [])

    for item in items:
        try:
            if item["mimeType"] == "application/vnd.google-apps.folder":
                # Recursively delete subfolder
                delete_drive_folder_recursive(service, item["id"])
            else:
                # Delete file
                service.files().delete(fileId=item["id"]).execute()
        except HttpError as e:
            # Skip files without permission
            print(f"Skipping {item['id']} due to {e}")

    # Finally delete the folder itself
    service.files().delete(fileId=folder_id).execute()


async def _fetch_all_files_recursive(service, folder_id: str, folder_name: Optional[str] = None):
    """Fetch folder tree recursively with all subfolders + files."""
    all_items = await run_in_threadpool(_drive_list_children, service, folder_id, None, None)

    folder_structure = {"id": folder_id, "name": folder_name, "subfolders": [], "files": []}

    for item in all_items:
        if item.get("mimeType") == FOLDER_MIME:
            # Recursive call for subfolder
            subfolder_data = await _fetch_all_files_recursive(service, item["id"], item["name"])
            folder_structure["subfolders"].append(subfolder_data)
        else:
            folder_structure["files"].append({
                "id": item["id"],
                "name": item["name"],
                "mimeType": item.get("mimeType")
            })

    # If folder name is not provided (root call), assign default
    if folder_structure["name"] is None:
        folder_structure["name"] = "Root"

    return folder_structure