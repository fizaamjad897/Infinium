"""
Query Reranker & Expander
- LLM-based query expansion for ambiguous questions
- Batch relevance scoring via LLM for result reranking
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
            f"{settings.OLLAMA_BASE_URL}/api/chat",
            json={
                "model": settings.LLM_MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a search query optimizer for a codebase search engine. "
                                   "Rewrite the user's question to be more specific and "
                                   "include relevant technical keywords that would help find the answer "
                                   "in source code, commits, and documentation. "
                                   "Return ONLY the rewritten query, nothing else.",
                    },
                    {
                        "role": "user",
                        "content": f"Original question: {original}\n\nRewritten query:",
                    },
                ],
                "stream": False,
                "options": {"temperature": 0.0, "num_ctx": 1024},
            },
            timeout=30,
        )
        response.raise_for_status()
        expanded = response.json().get("message", {}).get("content", "").strip()

        if expanded and len(expanded) < 500:
            print(f"   🔄 Query expanded: '{original}' → '{expanded[:100]}...'")
            return expanded
        return original

    except Exception as e:
        print(f"   ⚠️ Query expansion failed: {e}")
        return original


def rerank_chunks(query: str, chunks: list[dict], top_k: int = 5) -> list[dict]:
    """Use LLM to batch-score chunk relevance to the query.

    Sends all chunk previews to the LLM in one call and asks it
    to rank them by relevance. Falls back to existing scores if the
    LLM call fails.
    """
    if len(chunks) <= top_k:
        return chunks

    # Build a numbered list of chunk previews for the LLM
    previews = []
    for i, chunk in enumerate(chunks):
        preview = chunk["text"][:150].replace("\n", " ")
        source = chunk.get("source", "unknown")
        previews.append(f"{i+1}. [{source}]: {preview}")

    try:
        response = requests.post(
            f"{settings.OLLAMA_BASE_URL}/api/chat",
            json={
                "model": settings.LLM_MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a relevance scorer. Given a question and numbered text snippets, "
                                   "return ONLY the numbers of the most relevant snippets in order of relevance. "
                                   "Format: comma-separated numbers, e.g., '3,1,7,5,2'",
                    },
                    {
                        "role": "user",
                        "content": f"Question: {query}\n\nSnippets:\n" + "\n".join(previews)
                                   + f"\n\nReturn the top {top_k} most relevant snippet numbers:",
                    },
                ],
                "stream": False,
                "options": {"temperature": 0.0, "num_ctx": 4096},
            },
            timeout=30,
        )
        response.raise_for_status()
        ranking_text = response.json().get("message", {}).get("content", "").strip()

        # Parse the comma-separated numbers
        indices = []
        for part in ranking_text.replace(" ", "").split(","):
            try:
                idx = int(part) - 1  # Convert from 1-indexed to 0-indexed
                if 0 <= idx < len(chunks) and idx not in indices:
                    indices.append(idx)
            except ValueError:
                continue

        if indices:
            reranked = [chunks[i] for i in indices[:top_k]]
            print(f"   📊 Reranked {len(chunks)} chunks → top {len(reranked)}")
            return reranked

    except Exception as e:
        print(f"   ⚠️ Reranking failed, using default order: {e}")

    # Fallback: use existing scoring
    return chunks[:top_k]
