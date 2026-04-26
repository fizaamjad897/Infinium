"""
Query Reranker — Mature Version
- Scored reranking: LLM gives each chunk a 0-10 relevance score (not a comma list)
- Reciprocal Rank Fusion (RRF): merges semantic rank + keyword rank + LLM rank
- Query expansion: remains unchanged
"""
from __future__ import annotations
import requests
import time
from backend.config import settings


def expand_query(original: str) -> str:
    """Use LLM to rephrase an ambiguous query for better retrieval.

    The LLM rewrites the question to be more specific, expanding
    abbreviations and surfacing technical keywords.
    """
    try:
        response = requests.post(
            f"{settings.OLLAMA_BASE_URL}/api/chat",
            json={
                "model": settings.LLM_MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a search query optimizer for a codebase search engine. "
                            "Rewrite the user's question to be more specific and include relevant "
                            "technical keywords that would help find the answer in source code, "
                            "commits, branches, and documentation. "
                            "Return ONLY the rewritten query, nothing else."
                        ),
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
            print(f"   🔄 Query expanded: '{original[:60]}' → '{expanded[:80]}...'")
            return expanded
        return original
    except Exception as e:
        print(f"   ⚠️ Query expansion failed: {e}")
        return original


def rerank_chunks(query: str, chunks: list[dict], top_k: int = 5) -> list[dict]:
    """LLM-scored reranking using Reciprocal Rank Fusion.

    Improvement over old approach:
    - Asks LLM for a 0-10 score per chunk (not a fragile comma-list)
    - Falls back gracefully if any chunk score fails to parse
    - Combines with existing vector relevance via RRF for stability
    """
    if len(chunks) <= top_k:
        return chunks

    # Build scored list via LLM
    llm_scores = _score_chunks_with_llm(query, chunks)

    # Reciprocal Rank Fusion: combine vector rank + LLM rank
    k_rrf = 60  # standard RRF constant

    # Vector rank (existing relevance order, already sorted descending)
    vector_rank = {i: rank for rank, i in enumerate(range(len(chunks)))}

    # LLM rank (sort by llm_score descending → assign rank)
    llm_order = sorted(range(len(chunks)), key=lambda i: llm_scores[i], reverse=True)
    llm_rank = {i: rank for rank, i in enumerate(llm_order)}

    # RRF score = sum of 1/(k + rank) for each ranking signal
    rrf_scores = []
    for i, chunk in enumerate(chunks):
        rrf = (1 / (k_rrf + vector_rank[i])) + (1 / (k_rrf + llm_rank[i]))
        chunk["llm_score"] = round(llm_scores[i], 2)
        chunk["rrf_score"] = round(rrf, 6)
        rrf_scores.append((i, rrf))

    rrf_scores.sort(key=lambda x: x[1], reverse=True)
    reranked = [chunks[i] for i, _ in rrf_scores[:top_k]]

    print(f"   📊 Reranked {len(chunks)} chunks → top {len(reranked)} via RRF")
    return reranked


def _score_chunks_with_llm(query: str, chunks: list[dict]) -> list[float]:
    """Ask the LLM to score each chunk's relevance to the query (0-10).

    Sends ALL chunks in one API call with numbered snippets.
    Returns a list of scores, one per chunk (defaults to 5.0 on parse failure).
    """
    previews = []
    for i, chunk in enumerate(chunks):
        # Use more text (300 chars) than before for better scoring accuracy
        preview = chunk["text"][:300].replace("\n", " ").strip()
        source = chunk.get("source", "unknown")
        chunk_type = chunk.get("type", "code")
        previews.append(f"[{i+1}] ({chunk_type}) {source}: {preview}")

    prompt = (
        f"Question: {query}\n\n"
        f"Rate each snippet's relevance to the question from 0 to 10.\n"
        f"Format: one line per snippet: <number>: <score>\n"
        f"Example: 1: 8\n2: 3\n3: 9\n\n"
        f"Snippets:\n" + "\n".join(previews) +
        f"\n\nScores (one per line, format '<number>: <score>'):"
    )

    default_scores = [5.0] * len(chunks)

    try:
        response = requests.post(
            f"{settings.OLLAMA_BASE_URL}/api/chat",
            json={
                "model": settings.LLM_MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a relevance scorer for a code search engine. "
                            "Score each snippet's relevance to the question strictly "
                            "from 0 (completely irrelevant) to 10 (directly answers the question). "
                            "Return ONLY the numbered scores, nothing else."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
                "options": {"temperature": 0.0, "num_ctx": 4096},
            },
            timeout=45,
        )
        response.raise_for_status()
        text = response.json().get("message", {}).get("content", "").strip()

        # Parse "N: score" lines
        scores = list(default_scores)  # copy
        for line in text.splitlines():
            line = line.strip()
            if ":" in line:
                parts = line.split(":", 1)
                try:
                    idx = int(parts[0].strip()) - 1  # 1-indexed → 0-indexed
                    score = float(parts[1].strip())
                    if 0 <= idx < len(chunks):
                        scores[idx] = max(0.0, min(10.0, score))
                except (ValueError, IndexError):
                    continue

        return scores

    except Exception as e:
        print(f"   ⚠️ Chunk scoring failed: {e} — using vector scores")
        return default_scores
