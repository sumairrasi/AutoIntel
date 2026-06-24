# put this in a utils module
from typing import Any, Iterable, List, Dict, Optional


from langchain_core.documents import Document


def serialize_docs(ctx: Optional[Iterable[Any]], *, max_chars: int = 500) -> List[Dict[str, Any]]:
    """Convert a list of LangChain Documents (or dict-like) into JSON-safe dicts."""
    out: List[Dict[str, Any]] = []
    if not ctx:
        return out

    for item in ctx:
        # LangChain Document
        if isinstance(item, Document):
            md = item.metadata or {}
            out.append({
                "source": md.get("source"),
                "doc_id": md.get("doc_id"),
                "file_id": md.get("file_id"),
                "page": md.get("page") or md.get("page_number") or md.get("page_index"),
                "total_pages": md.get("total_pages"),
                "collection": md.get("_collection_name"),
                "snippet": (item.page_content or "")[:max_chars],
            })
            continue

        # If it already looks like a dict with page_content/metadata
        if isinstance(item, dict):
            md = item.get("metadata", {}) or {}
            pc = item.get("page_content") or ""
            out.append({
                "source": md.get("source"),
                "doc_id": md.get("doc_id"),
                "file_id": md.get("file_id"),
                "page": md.get("page") or md.get("page_number") or md.get("page_index"),
                "total_pages": md.get("total_pages"),
                "collection": md.get("_collection_name"),
                "snippet": str(pc)[:max_chars],
            })
            continue

        # Fallback: keep something human-readable
        out.append({"value": repr(item)[:max_chars]})
    return out
