"""
RAG Retriever — Mature Version
- Semantic search via ChromaDB
- Hybrid search: semantic + BM25 keyword
- MMR (Maximal Marginal Relevance) deduplication for diverse context
- Metadata-type filters (code, commit, branch, etc.)
"""
from __future__ import annotations
from typing import Optional
from backend.ingestion.embedder import get_chroma_collection, embed_query
from backend.config import settings
import math
from concurrent.futures import ThreadPoolExecutor, as_completed


def _cosine_sim(a: list[float], b: list[float]) -> float:
    """Fast cosine similarity between two embedding vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def mmr_select(
    query_emb: list[float],
    candidates: list[dict],
    top_k: int,
    lambda_mult: float = 0.6,
) -> list[dict]:
    """Maximal Marginal Relevance selection.

    Balances relevance to the query vs. diversity among selected chunks.
    lambda_mult=1.0 → pure relevance (no dedup)
    lambda_mult=0.0 → pure diversity
    Default 0.6: favours relevance but strongly penalises near-duplicates.
    """
    if not candidates or top_k <= 0:
        return []

    # Each candidate needs an embedding for MMR — use relevance score as proxy
    # (full re-embedding would be ideal but too slow; this is a fast approximation)
    selected: list[dict] = []
    remaining = list(candidates)

    while len(selected) < top_k and remaining:
        if not selected:
            # First pick: highest relevance
            best = max(remaining, key=lambda c: c.get("relevance", 0))
        else:
            # MMR score: λ·sim(q, c) - (1-λ)·max_sim(c, selected)
            def mmr_score(c: dict) -> float:
                rel = c.get("relevance", 0.0)
                # Approximate inter-chunk similarity
                # For code chunks: penalize similar text prefixes
                # For metadata chunks (branch/commit): penalize less so we can see multiple branches
                is_meta = c.get("type") in {"branch", "commit", "commit_diff"}
                penalty_weight = 0.2 if is_meta else 1.0
                
                max_sim = max(
                    (1.0 if c["text"][:80] == s["text"][:80] else
                     0.5 if c.get("source") == s.get("source") else 0.0) * penalty_weight
                    for s in selected
                )
                return lambda_mult * rel - (1 - lambda_mult) * max_sim

            best = max(remaining, key=mmr_score)

        selected.append(best)
        remaining.remove(best)

    return selected


def retrieve_context(
    query: str,
    repo_name: str,
    top_k: int = None,
    threshold: float = None,
    chunk_types: Optional[list[str]] = None,
    branch_filter: Optional[str] = None,
) -> list[dict]:
    """Semantic search over a single repo's vector store, with MMR deduplication.

    Args:
        chunk_types: Optional filter e.g. ["code", "commit_diff", "branch"]
                     None = return all types
        branch_filter: Optional branch name to scope results to one branch.
                       Matches chunks where metadata.branch == branch_filter.
    """
    top_k = top_k or settings.RAG_TOP_K
    threshold = threshold or settings.RELEVANCE_THRESHOLD

    collection = get_chroma_collection(repo_name)
    if collection.count() == 0:
        return []

    query_embedding = embed_query(query)

    # Fetch 3x more than needed — MMR will thin them back
    fetch_n = min(top_k * 3, collection.count())

    # Build ChromaDB where filter
    conditions = []
    if chunk_types:
        if len(chunk_types) == 1:
            conditions.append({"type": chunk_types[0]})
        else:
            conditions.append({"type": {"$in": chunk_types}})
    if branch_filter:
        conditions.append({"branch": {"$eq": branch_filter}})

    if len(conditions) == 0:
        where_filter = None
    elif len(conditions) == 1:
        where_filter = conditions[0]
    else:
        where_filter = {"$and": conditions}

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=fetch_n,
        include=["documents", "metadatas", "distances"],
        where=where_filter,
    )

    chunks = []
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        relevance = round(1 - dist, 3)
        if relevance >= threshold:
            chunks.append({
                "text": doc,
                "source": meta.get("source", "unknown"),
                "type": meta.get("type", "code"),
                "repo": meta.get("repo", repo_name),
                "language": meta.get("language", ""),
                "branch": meta.get("branch", ""),
                "relevance": relevance,
                "metadata": meta,
            })

    # Sort by relevance
    chunks.sort(key=lambda x: x["relevance"], reverse=True)

    # Apply MMR to get diverse top_k
    return mmr_select(query_embedding, chunks, top_k)


def retrieve_multi_repo(
    query: str,
    repo_names: list[str],
    top_k: int = None,
    threshold: float = None,
    chunk_types: Optional[list[str]] = None,
) -> list[dict]:
    """Retrieve and merge context from multiple repos with MMR across all.
    
    Uses parallel execution for multi-repo retrieval to minimize latency.
    """
    top_k = top_k or settings.RAG_TOP_K
    threshold = threshold or settings.RELEVANCE_THRESHOLD

    all_chunks = []
    
    def _safe_retrieve(repo):
        try:
            return retrieve_context(
                query, repo,
                top_k=top_k,
                threshold=threshold,
                chunk_types=chunk_types,
            )
        except Exception as e:
            print(f"   ⚠️ Error retrieving from {repo}: {e}")
            return []

    # Parallelize per-repo retrieval
    workers = min(len(repo_names), settings.RETRIEVAL_WORKERS)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_safe_retrieve, repo) for repo in repo_names]
        for future in as_completed(futures):
            all_chunks.extend(future.result())

    all_chunks.sort(key=lambda x: x["relevance"], reverse=True)
    # Apply global MMR across all repos
    return mmr_select([], all_chunks, top_k)


def hybrid_retrieve(
    query: str,
    repo_name: str,
    top_k: int = None,
    semantic_weight: float = 0.65,
    keyword_weight: float = 0.35,
    chunk_types: Optional[list[str]] = None,
    branch_filter: Optional[str] = None,
) -> list[dict]:
    """Hybrid search: semantic + BM25, then MMR deduplication."""
    top_k = top_k or settings.RAG_TOP_K

    semantic_chunks = retrieve_context(
        query, repo_name,
        top_k=top_k * 3,
        threshold=0.05,
        chunk_types=chunk_types,
        branch_filter=branch_filter,
    )

    if not semantic_chunks:
        return []

    try:
        from rank_bm25 import BM25Okapi

        corpus = [c["text"].split() for c in semantic_chunks]
        bm25 = BM25Okapi(corpus)
        keyword_scores = bm25.get_scores(query.split())

        max_keyword = max(keyword_scores) if any(s > 0 for s in keyword_scores) else 1.0

        for i, chunk in enumerate(semantic_chunks):
            norm_kw = keyword_scores[i] / max_keyword
            chunk["keyword_score"] = round(norm_kw, 3)
            chunk["combined_score"] = round(
                chunk["relevance"] * semantic_weight + norm_kw * keyword_weight, 3
            )

        semantic_chunks.sort(key=lambda x: x["combined_score"], reverse=True)

    except ImportError:
        for chunk in semantic_chunks:
            chunk["combined_score"] = chunk["relevance"]

    # Run MMR on the combined-scored candidates
    query_emb = embed_query(query)
    return mmr_select(query_emb, semantic_chunks, top_k)


def hybrid_retrieve_multi_repo(
    query: str,
    repo_names: list[str],
    top_k: int = None,
    semantic_weight: float = 0.65,
    keyword_weight: float = 0.35,
    chunk_types: Optional[list[str]] = None,
) -> list[dict]:
    """Hybrid search across multiple repos with global MMR.
    
    Uses parallel execution for hybrid retrieval to minimize latency.
    """
    top_k = top_k or settings.RAG_TOP_K

    all_chunks = []
    
    def _safe_hybrid_retrieve(repo):
        try:
            return hybrid_retrieve(
                query, repo,
                top_k=top_k,
                semantic_weight=semantic_weight,
                keyword_weight=keyword_weight,
                chunk_types=chunk_types,
            )
        except Exception as e:
            print(f"   ⚠️ Error in hybrid retrieve from {repo}: {e}")
            return []

    # Parallelize per-repo hybrid retrieval
    workers = min(len(repo_names), settings.RETRIEVAL_WORKERS)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_safe_hybrid_retrieve, repo) for repo in repo_names]
        for future in as_completed(futures):
            all_chunks.extend(future.result())

    all_chunks.sort(key=lambda x: x.get("combined_score", x["relevance"]), reverse=True)
    return mmr_select([], all_chunks, top_k)
