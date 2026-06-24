import os
from dotenv import load_dotenv, find_dotenv

env_path = find_dotenv(usecwd=True)  
load_dotenv(env_path, verbose=True)

os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
DATABASE_URL = os.getenv("DATABASE_URL", "")
DATABASE_SYNC_URL = os.getenv("DATABASE_SYNC_URL", "")
OPENAI_API_KEY  = os.getenv("OPENAI_API_KEY")
COLLECTION_NAME = os.getenv("COLLECTION_NAME")
QDRANT_HOST = os.getenv("QDRANT_HOST")
CLIENT_SECRET_FILE = os.getenv("CLIENT_SECRET_FILE", "/run/credentials/client_secret.json")
TOKEN_FILE         = os.getenv("TOKEN_FILE", "/run/tokens/token.json")
REDIRECT_URI       = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/oauth2callback")
QDRANT_PORT = os.getenv("QDRANT_PORT")
QDRANT_URL = os.getenv("QDRANT_URL")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL")
RAG_MODEL = os.getenv("RAG_MODEL")
THRESHOLD = int(os.getenv("THRESHOLD"))
# \
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB   = int(os.getenv("REDIS_DB", "0"))
REDIS_URL  = os.getenv("REDIS_URL", f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}")
GATEWAY_BASE_URL = 'https://apiband.sblcorp.com'

ISSUER = "https://authband.sblcorp.com/realms/banditos"

JWKS_URL = "https://authband.sblcorp.com/realms/banditos/protocol/openid-connect/certs"

SCOPES = os.getenv("SCOPES", "https://www.googleapis.com/auth/drive.file")
FOLDER_ID = os.getenv("FOLDER_ID")
TEMP_DIR = "temp_uploads"
VECTOR_DB = "QdrantVectorStore"
ENCODING_NAME         = "cl100k_base"
EMBEDDING_COST_RATE   = 0.00013 

EUREKA_SERVER = "http://localhost:8761/eureka/apps"
SERVICE_NAME = "bandistonic-service"
HOST = "127.0.0.1"   # your FastAPI server IP
PORT = 8280               # your FastAPI app port
INSTANCE_ID = f"{HOST}:{SERVICE_NAME}:{PORT}"
