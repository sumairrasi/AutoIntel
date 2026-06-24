from fastapi import Depends, HTTPException
import base64, json, requests
from jose import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.config.constant import JWKS_URL,ISSUER
import os
from app.db.models import Document
from app.config.constant import TOKEN_FILE,TEMP_DIR
from typing import List, Optional
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import io
from sqlalchemy.orm import Session
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleRequest
security = HTTPBearer()


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
    
    
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from app.config.constant import TOKEN_FILE, SCOPES, FOLDER_ID
from app.utils.google_drive_utils import _scopes_list

def get_drive_service():
    creds = Credentials.from_authorized_user_file(TOKEN_FILE, scopes=_scopes_list())
    return build('drive', 'v3', credentials=creds)

def get_folder_storage_usage(folder_id: str):
    service = get_drive_service()

    # Get total Drive quota
    about_info = service.about().get(fields="storageQuota").execute()
    quota = about_info.get("storageQuota", {})
    total_gb = int(quota.get("limit", 0)) / (1024 ** 3)
    # Sum file sizes in the folder
    total_size_bytes = 0
    page_token = None
    while True:
        results = service.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields="nextPageToken, files(size)",
            pageToken=page_token
        ).execute()

        for file in results.get("files", []):
            if "size" in file:
                total_size_bytes += int(file["size"])

        page_token = results.get("nextPageToken")
        if not page_token:
            break

    used_gb = total_size_bytes / (1024 ** 3)
    remaining_gb = total_gb - used_gb
    percent_used = (used_gb / total_gb * 100) if total_gb > 0 else 0

    return {
        "total_gb": round(total_gb, 2),
        "used_gb": round(used_gb, 2),
        "remaining_gb": round(remaining_gb, 2),
        "percent_used": round(percent_used, 2)
    }
    
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
    
    
def download_file_from_drive(file_id: str, creds) -> io.BytesIO:
    drive_service = build("drive", "v3", credentials=creds)

    request = drive_service.files().get_media(fileId=file_id)
    file_stream = io.BytesIO()
    downloader = MediaIoBaseDownload(file_stream, request)

    done = False
    while not done:
        _, done = downloader.next_chunk()

    file_stream.seek(0)
    return file_stream

def download_files_to_temp_dir(
    db: Session,
    *,
    filenames: Optional[List[str]] = None,
    ids: Optional[List[int]] = None
) -> List[str]:
    """
    Downloads files (from Google Drive) to a local temp directory by filename or document ID.
    Returns list of local file paths.
    """
    print("ids:", ids)
    if not filenames and not ids:
        raise ValueError("Either 'filenames' or 'ids' must be provided.")

    query = db.query(Document)
    if filenames:
        query = query.filter(Document.filename.in_(filenames))
    elif ids:
        query = query.filter(Document.id.in_(ids))

    documents = query.all()

    if not documents:
        raise HTTPException(status_code=404, detail="No matching documents found")

    os.makedirs(TEMP_DIR, exist_ok=True)

    creds = load_credentials()
    if not creds or not creds.valid:
        raise HTTPException(status_code=401, detail="Authorization failed for Google Drive")

    downloaded_paths = []

    for doc in documents:
        file_id = doc.meta_data.get("file_id")
        file_name = doc.filename
        print("fileid is", file_id, "file_name", file_name)

        if not file_id:
            continue  

        output_path = os.path.join(TEMP_DIR, file_name)

        if not os.path.exists(output_path):
            try:
                file_stream = download_file_from_drive(file_id, creds)
                with open(output_path, "wb") as f:
                    f.write(file_stream.read())
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Download failed for {file_name}: {str(e)}")

        downloaded_paths.append(output_path)
    return downloaded_paths
