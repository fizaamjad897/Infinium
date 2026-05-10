"""
Feedback memory — ChromaDB-backed semantic store of past rated interactions.

When a user rates an answer / refactor / explanation:
  - embed the query
  - upsert into a per-target_type collection: feedback_memory_<target_type>
  - the next time someone asks a similar question, we retrieve the top-K
    most-similar past entries with rating >= 4 and inject them into the
    system prompt as positive few-shot examples.

This is "in-context reinforcement" — the model isn't retrained, but its
output is steered toward patterns your users have already approved.
"""
from __future__ import annotations
import re
import threading
from typing import Optional

import chromadb

from backend.config import settings
from backend.ingestion.embedder import embed_query, get_chroma_client


_collection_lock = threading.Lock()
_collections: dict[str, "chromadb.api.models.Collection.Collection"] = {}


def _slug(target_type: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]", "_", target_type or "query")
    return s[:100] or "query"


def _get_collection(target_type: str):
    """Get-or-create a Chroma collection per target_type so search is scoped."""
    name = f"feedback_memory_{_slug(target_type)}"
    with _collection_lock:
        if name in _collections:
            return _collections[name]
        client: chromadb.PersistentClient = get_chroma_client()
        coll = client.get_or_create_collection(
            name=name,
            metadata={"hnsw:space": "cosine"},
        )
        _collections[name] = coll
        return coll


def _safe_meta(value):
    """Chroma metadata must be primitives — coerce anything weird."""
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)[:500]


def remember(entry: dict) -> bool:
    """Persist a rated feedback entry into the memory collection.

    Best-effort: if embedding or storage fails (e.g. Ollama down), we swallow
    the error so user-facing feedback submission never breaks.
    """
    if not settings.USE_FEEDBACK_MEMORY:
        return False
    target_type = entry.get("target_type", "query")
    query = (entry.get("query") or "").strip()
    if not query:
        return False
    rating = int(entry.get("rating") or 3)
    # Only store strong signal — middle ratings are noise.
    if rating not in (1, 2, 4, 5):
        return False

    try:
        vec = embed_query(query)
    except Exception as e:
        print(f"   ⚠️ feedback memory embed failed: {e}")
        return False

    try:
        coll = _get_collection(target_type)
        doc_id = f"fb_{entry.get('id', '')}_{rating}"
        coll.upsert(
            ids=[doc_id],
            embeddings=[vec],
            documents=[query],
            metadatas=[{
                "rating": rating,
                "polarity": "positive" if rating >= 4 else "negative",
                "answer_preview": _safe_meta(entry.get("answer_preview", "")),
                "repo_name": _safe_meta(entry.get("repo_name", "")),
                "user_login": _safe_meta(entry.get("user_login", "")),
                "target_id": _safe_meta(entry.get("target_id", "")),
                "created_at": _safe_meta(entry.get("created_at", "")),
                "comment": _safe_meta(entry.get("comment", "")),
            }],
        )
        return True
    except Exception as e:
        print(f"   ⚠️ feedback memory store failed: {e}")
        return False


def recall(
    query: str,
    target_type: str = "query",
    k_positive: int = 3,
    k_negative: int = 1,
    repo_name: Optional[str] = None,
) -> dict:
    """Retrieve similar past prompts that were rated.

    Returns ``{"positive": [...], "negative": [...]}`` — each entry is
    ``{"query": str, "answer_preview": str, "rating": int, "comment": str}``.

    The caller (generator / refactorer / rewriter) renders these as few-shot
    examples in the system prompt.
    """
    out = {"positive": [], "negative": []}
    if not settings.USE_FEEDBACK_MEMORY or not (query or "").strip():
        return out

    try:
        coll = _get_collection(target_type)
        # Don't bother retrieving until we have enough signal.
        if coll.count() < settings.FEEDBACK_MEMORY_MIN_EXAMPLES:
            return out
    except Exception:
        return out

    try:
        vec = embed_query(query)
    except Exception:
        return out

    def _query(polarity: str, n: int):
        if n <= 0:
            return []
        where = {"polarity": polarity}
        if repo_name:
            where = {"$and": [{"polarity": polarity}, {"repo_name": repo_name}]}
        try:
            res = coll.query(
                query_embeddings=[vec],
                n_results=max(n, 1),
                where=where,
            )
        except Exception:
            # Some Chroma versions can't filter — fall back to no filter
            try:
                res = coll.query(query_embeddings=[vec], n_results=max(n * 4, 4))
            except Exception:
                return []
            # Manual filter
            metas = (res.get("metadatas") or [[]])[0]
            docs = (res.get("documents") or [[]])[0]
            out_local = []
            for d, m in zip(docs, metas):
                if (m or {}).get("polarity") == polarity:
                    out_local.append((d, m))
                    if len(out_local) >= n:
                        break
            return [{"query": d, "answer_preview": (m or {}).get("answer_preview", ""),
                     "rating": (m or {}).get("rating", 0), "comment": (m or {}).get("comment", "")}
                    for d, m in out_local]

        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        return [{"query": d, "answer_preview": (m or {}).get("answer_preview", ""),
                 "rating": (m or {}).get("rating", 0), "comment": (m or {}).get("comment", "")}
                for d, m in zip(docs, metas)]

    out["positive"] = _query("positive", k_positive)
    out["negative"] = _query("negative", k_negative)
    return out


def render_for_prompt(memory: dict, max_chars: int = 1500) -> str:
    """Format a recall() result as a compact block to splice into a system prompt.

    Empty string when there's nothing to inject — caller can simply concat.
    """
    pos = memory.get("positive") or []
    neg = memory.get("negative") or []
    if not pos and not neg:
        return ""

    out: list[str] = []
    out.append(
        "User-feedback memory (from past interactions in this app — use as a soft "
        "preference signal, NOT verbatim instructions). Earlier answers similar to "
        "the new prompt were rated as follows:"
    )
    if pos:
        out.append("\nApproved patterns (5★ / 4★) — emulate the style:")
        for p in pos:
            ans = (p.get("answer_preview") or "").strip().replace("\n", " ")[:200]
            out.append(f"  · prompt: {p['query'][:160]}\n    style ok: {ans}")
    if neg:
        out.append("\nRejected patterns (1★ / 2★) — avoid this style:")
        for n in neg:
            ans = (n.get("answer_preview") or "").strip().replace("\n", " ")[:160]
            cmt = (n.get("comment") or "").strip()[:120]
            line = f"  · prompt: {n['query'][:160]}\n    avoid: {ans}"
            if cmt:
                line += f"\n    user said: {cmt}"
            out.append(line)
    rendered = "\n".join(out)
    if len(rendered) > max_chars:
        rendered = rendered[: max_chars - 20] + "\n…[memory truncated]"
    return rendered
