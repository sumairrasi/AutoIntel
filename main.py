import base64, json, requests
from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend

app = FastAPI()
security = HTTPBearer()
# ISSUER = "https://auth.dev.gbs-plus.com/realms/gbs"

ISSUER = "https://auth.gbs-plus.com/realms/gbs-platform"
JWKS_URL = "https://auth.gbs-plus.com/realms/gbs-platform/protocol/openid-connect/certs"

jwks = requests.get(JWKS_URL).json()

def get_kid(token: str):
    header_b64 = token.split(".")[0]
    padded = header_b64 + "=" * (-len(header_b64) % 4)
    header_json = base64.urlsafe_b64decode(padded.encode()).decode()
    return json.loads(header_json)["kid"]

def get_public_key(token: str):
    kid = get_kid(token)
    for key in jwks["keys"]:
        if key["kid"] == kid:
            n = int.from_bytes(base64.urlsafe_b64decode(key["n"] + "=="), "big")
            e = int.from_bytes(base64.urlsafe_b64decode(key["e"] + "=="), "big")
            public_key = rsa.RSAPublicNumbers(e, n).public_key(default_backend())
            pem = public_key.public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo
            )
            return pem.decode("utf-8")
    raise HTTPException(status_code=401, detail="Invalid key ID")

from pydantic import BaseModel
from typing import Dict

class AuthenticatedUser(BaseModel):
    claims: Dict
    raw_token: str




def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials.strip()

    if "." not in token:
        raise HTTPException(status_code=401, detail="Invalid token format (not JWT)")

    try:
        public_key = get_public_key(token)
        claims = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            issuer=ISSUER,
            options={"verify_aud": False}
        )
        return {
            "claims": claims,
            "raw_token": token
        }
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Token validation failed: {str(e)}")


@app.get("/secure-data")
def secure_data(user: dict = Depends(verify_token)):
    return {"message": "✅ Token is valid", "user": user}


GATEWAY_BASE_URL = 'https://gateway.gbs-plus.com'


from typing import List, Optional
from pydantic import BaseModel

class UserFilterRequest(BaseModel):
    ids: Optional[List[int]] = None
    page: int = 0
    size: int = 100




import jwt  # pip install pyjwt

def decode_token(token: str):
    try:
        payload = jwt.decode(token, options={"verify_signature": False})
        print("JWT Payload:", payload)
    except Exception as e:
        print("Error decoding token:", e)



@app.post("/user-service/api/user/find-by-ids")
def proxy_find_by_ids(req: UserFilterRequest, user: dict = Depends(verify_token)):
    token = user["raw_token"]
    decode_token(token)
    url = f"{GATEWAY_BASE_URL}/user-service/api/user/find-list"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    # still send a normal request (all data)
    payload = {
        "searchTerm": "",
        "searchFields": [],
        "sortBy": "createdOn",
        "sortDirection": "DESC",
        "page": req.page,
        "size": req.size,
        "filters": {}
    }

    resp = requests.post(url, headers=headers, json=payload)
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    result = resp.json()
    print("result: ",result)
    # post-filter the response data if ids are provided
    if req.ids:
        filtered_data = [
            item for item in result.get("data", [])
            if item.get("id") in req.ids
        ]
        result["data"] = filtered_data
        result["totalCount"] = len(filtered_data)

    return result




from datetime import datetime
import os
import io
import fitz
import tempfile
from typing import List, Optional,Tuple
from aiohttp import Payload
from fastapi import APIRouter, Body, Path, Query, UploadFile, File, Request, Response, HTTPException, Depends, Form
from fastapi.responses import RedirectResponse
import fitz
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.responses import StreamingResponse
from googleapiclient.http import MediaIoBaseDownload
from app.db.database import get_db
from datetime import datetime
from sqlalchemy import extract
from sqlalchemy.orm import selectinload
from sqlalchemy import func
from fastapi import Query
from app.utils.util_file import (

    load_credentials,

)

from app.config.constant import CLIENT_SECRET_FILE,SCOPES,REDIRECT_URI,TOKEN_FILE,FOLDER_ID
# Configuration
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
def _scopes_list():
    return SCOPES if isinstance(SCOPES, (list, tuple)) else str(SCOPES).split()

def save_creds(creds: Credentials) -> None:
    with open(TOKEN_FILE, "w") as f:
        f.write(creds.to_json())

def load_credentials() -> Optional[Credentials]:
    if not os.path.isfile(TOKEN_FILE):
        print("no path::")
        return None
    try:
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, scopes=_scopes_list())
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(GoogleRequest())
                save_creds(creds)
            except Exception:
                print("expired....")
                return None
        return creds
    except Exception:
        print("soem erorr")
        return None


FOLDER_MIME = "application/vnd.google-apps.folder"

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
 

@app.post("/folders/subfolders")
async def list_folders(
    folder_id: Optional[str] = Body(None, embed=True),
    file_flag: bool = Query(False, description="If true, include files in the response")
):
    # Load credentials
    creds = load_credentials()
    print("creds: ",creds)
    if not creds or not creds.valid:
        raise HTTPException(
            status_code=401,
            detail="Authorization required. Click Authorize in /ui."
        )

    # Initialize Drive service
    drive_service = build("drive", "v3", credentials=creds)

    # Determine parent folder
    parent_drive_id = folder_id if folder_id else FOLDER_ID
    print("Parent Drive ID:", parent_drive_id)

    # Fetch subfolders
    subfolders = await run_in_threadpool(
        _drive_list_children,
        drive_service,
        parent_drive_id,
        FOLDER_MIME,
        None
    )
    # subfolders = _drive_list_children(drive_service, parent_drive_id, mime_filter=FOLDER_MIME)

    print("\nFinal count:", len(subfolders))


    print("subfolder",subfolders)


    # Case: file_flag=True and folder_id is provided → fetch files
    if file_flag and folder_id:
        all_items = await run_in_threadpool(
            _drive_list_children,
            drive_service,
            parent_drive_id,
            None,
            None
        )
        print([f["name"] for f in all_items])
        files = [f for f in all_items if f.get("mimeType") != FOLDER_MIME]
        return {
            "status": "success",
            "parentId": folder_id,
            "folders": [{"id": f["id"], "name": f["name"]} for f in subfolders],
            "files": [{"id": f["id"], "name": f["name"], "mimeType": f.get("mimeType")} for f in files]
        }

    # Default response: only folders
    return {
        "status": "success",
        "parentId": folder_id or None,
        "folders": [{"id": f["id"], "name": f["name"]} for f in subfolders]
    }
    
    
    
