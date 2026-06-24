from app.config.constant import OPENAI_API_KEY,COLLECTION_NAME,QDRANT_HOST,QDRANT_PORT
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, VectorParams
from langchain_openai import OpenAIEmbeddings
from langchain_qdrant import FastEmbedSparse, RetrievalMode
from qdrant_client.http.models import Distance, SparseVectorParams, VectorParams
from qdrant_client import QdrantClient, models


client = QdrantClient(host=QDRANT_HOST,timeout=900.0,port=QDRANT_PORT)


sparse_embeddings = FastEmbedSparse(model_name="Qdrant/bm25")


embeddings = OpenAIEmbeddings(model="text-embedding-3-large",api_key=OPENAI_API_KEY)

existing_collections = client.get_collections().collections
existing_names = {collection.name for collection in existing_collections}

# Conditionally create if not already present
if COLLECTION_NAME not in existing_names:
    client.create_collection(
    collection_name=COLLECTION_NAME,
    vectors_config={"dense": VectorParams(size=3072, distance=Distance.COSINE)},
    sparse_vectors_config={
        "sparse": SparseVectorParams(index=models.SparseIndexParams(on_disk=False))
        },
    )



vector_store = QdrantVectorStore(
    client=client,
    collection_name=COLLECTION_NAME,
    embedding=embeddings,
    sparse_embedding=sparse_embeddings,
    retrieval_mode=RetrievalMode.HYBRID,
    vector_name="dense",
    sparse_vector_name="sparse",
)
