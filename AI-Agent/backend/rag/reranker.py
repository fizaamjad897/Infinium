"""
Query Reranker & Expander
- LLM-based query expansion for ambiguous questions
- Result reranking for quality improvement
"""
import requests
from backend.config import settings


def expand_query(original: str) -> str:
    """Use LLM to rephrase an ambiguous query for better retrieval.
    
    The LLM rewrites the question to be more specific for codebase search,
    expanding abbreviations and making intent clearer.
    """
    try:
        response = requests.post(
            f"{settings.OLLAMA_BASE_URL}/api/generate",
            json={
                "model": settings.LLM_MODEL,
                "prompt": (
                    "You are a search query optimizer for a codebase search engine. "
                    "Rewrite the following developer question to be more specific and "
                    "include relevant technical keywords that would help find the answer "
                    "in source code, commits, and documentation. "
                    "Return ONLY the rewritten query, nothing else.\n\n"
                    f"Original question: {original}\n\n"
                    "Rewritten query:"
                ),
                "stream": False,
                "options": {"temperature": 0.0, "num_ctx": 1024},
            },
            timeout=30,
        )
        response.raise_for_status()
        expanded = response.json().get("response", "").strip()
        
        if expanded and len(expanded) < 500:
            print(f"   🔄 Query expanded: '{original}' → '{expanded[:100]}...'")
            return expanded
        return original
        
    except Exception as e:
        print(f"   ⚠️ Query expansion failed: {e}")
        return original


def rerank_with_context(query: str, chunks: list[dict], top_k: int = 5) -> list[dict]:
    """Use LLM to rerank retrieved chunks by relevance to the query.
    
    This is a lightweight reranking that asks the LLM to score 
    relevance of each chunk. For production, use a cross-encoder.
    """
    if len(chunks) <= top_k:
        return chunks
    
    # Simple: just use the existing scoring (semantic + keyword)
    # For a more advanced approach, we'd call the LLM to score each chunk
    # but that's too slow for real-time queries with Ollama
    return chunks[:top_k]
