import tiktoken
from app.db.database_sync import SessionLocal   
from app.db.models import Document, Token, Status
from datetime import datetime
import pytz
from app.config.celery_app import celery_app
from app.utils.token_utils import download_files_to_temp_dir
from app.utils.ingect_utils import IngectDataClass, ExtractTextClass
from app.config.vectordb_config import vector_store
from app.config.constant import EMBEDDING_COST_RATE


import redis, threading, time
from contextlib import contextmanager

r = redis.Redis(host="localhost",
    port=6379,
    db=0,
    decode_responses=True)

def _extend_lock(key: str, expire: int, stop_event: threading.Event):
    """
    Background thread: refresh lock expiration every expire/2 seconds.
    """
    while not stop_event.is_set():
        # Refresh the TTL of the lock
        r.expire(key, expire)
        time.sleep(expire // 2)

@contextmanager
def redis_lock(key: str, expire: int = 300):
    """
    Acquire a Redis lock with auto-extension until released.
    """
    acquired = bool(r.set(name=key, value="1", nx=True, ex=expire))
    stop_event = threading.Event()
    thread = None

    if acquired:
        # Start background thread to extend TTL
        thread = threading.Thread(target=_extend_lock, args=(key, expire, stop_event))
        thread.daemon = True
        thread.start()

    try:
        yield acquired
    finally:
        if acquired:
            # Stop refresher and release lock
            stop_event.set()
            if thread:
                thread.join(timeout=1)
            try:
                r.delete(key)
            except Exception:
                pass


@celery_app.task(name="tasks.inject_verified_documents")
def inject_verified_documents_task():
    with redis_lock("inject_docs_lock", expire=1800) as acquired:
        if not acquired:
            print("Another worker is already processing. Skipping this run.")
            return {"skipped": True}

        tokenizer = tiktoken.get_encoding("cl100k_base")

        with SessionLocal() as db:  # sync session
            # Fetch VERIFIED docs
            docs = db.query(Document).filter(Document.status == Status.VERIFIED).limit(3).all()
            if not docs:
                print("No VERIFIED documents to process.")
                return {"processed": []}

            # Download files locally (must be sync!)
            file_paths = download_files_to_temp_dir(db, ids=[d.id for d in docs])

            processed_ids = []
            for doc, file in zip(docs, file_paths):
                print(f"Processing doc {doc.id}, type={doc.doc_type}")
                thumbnail_path = doc.thumbnail_path

                if doc.doc_type == "normal":
                    extract_text = ExtractTextClass()
                    doc_text = extract_text.pdf_to_langchain_docs(file, doc.id, doc.file_id)

                    for d in doc_text:
                        d.metadata = d.metadata or {}
                        d.metadata["thumbnail_path"] = thumbnail_path

                    split_chunks = extract_text.split_into_chunks(doc_text)
                    for c in split_chunks:
                        c.metadata = c.metadata or {}
                        c.metadata["thumbnail_path"] = thumbnail_path

                    extract_text.store_vdb(vector_store, split_chunks)

                else:  # image
                    ingect_data = IngectDataClass()
                    doc_text = ingect_data.image_to_text(file, doc.id, doc.file_id)

                    for d in doc_text:
                        d.metadata = d.metadata or {}
                        d.metadata["thumbnail_path"] = thumbnail_path

                    split_chunks = ingect_data.split_into_chunks(doc_text)
                    for c in split_chunks:
                        c.metadata = c.metadata or {}
                        c.metadata["thumbnail_path"] = thumbnail_path

                    ingect_data.store_vdb(vector_store, split_chunks)

                # Token calculation
                tokens_by_chunk = {
                    idx: len(tokenizer.encode(chunk_doc.page_content))
                    for idx, chunk_doc in enumerate(split_chunks)
                }
                total_tokens = sum(tokens_by_chunk.values())
                chunk_count = len(split_chunks)
                cost = (total_tokens / 1000) * EMBEDDING_COST_RATE

                page_wise_mapping = {f"chunk_{p}": c for p, c in tokens_by_chunk.items()}
                token_entry = Token(
                    document_id=doc.id,
                    total_token=total_tokens,
                    page_wise_token=page_wise_mapping,
                    chunk_count=chunk_count,
                    cost=round(cost, 6),
                )
                db.add(token_entry)

                # Update doc
                doc.status = Status.INJECTED
                doc.injected_time = datetime.now(pytz.timezone("Asia/Kolkata"))
                processed_ids.append(doc.id)

            db.commit()
            print(f"Injected {len(processed_ids)} docs")
            return {"processed": processed_ids}
