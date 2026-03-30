"""
RAG Retriever
- Semantic search via ChromaDB
- Hybrid search: semantic + BM25 keyword matching
- Relevance filtering
"""
from typing import Optional
from backend.ingestion.embedder import get_chroma_collection, embed_query
from backend.config import settings


def retrieve_context(
    query: str,
    repo_name: str,
    top_k: int = None,
    threshold: float = None,
) -> list[dict]:
    """Semantic search over a single repo's vector store.
    
    Returns ranked chunks with relevance scores.
    """
    top_k = top_k or settings.RAG_TOP_K
    threshold = threshold or settings.RELEVANCE_THRESHOLD
    
    collection = get_chroma_collection(repo_name)
    
    if collection.count() == 0:
        return []
    
    # Embed query using sentence-transformers
    query_embedding = embed_query(query)
    
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=min(top_k * 2, collection.count()),  # fetch more for filtering
        include=["documents", "metadatas", "distances"],
    )
    
    chunks = []
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        relevance = round(1 - dist, 3)  # cosine distance → similarity
        if relevance >= threshold:
            chunks.append({
                "text": doc,
                "source": meta.get("source", "unknown"),
                "type": meta.get("type", "code"),
                "repo": meta.get("repo", repo_name),
                "relevance": relevance,
                "metadata": meta,
            })
    
    # Sort by relevance descending
    chunks.sort(key=lambda x: x["relevance"], reverse=True)
    return chunks[:top_k]


def retrieve_multi_repo(
    query: str,
    repo_names: list[str],
    top_k: int = None,
    threshold: float = None,
) -> list[dict]:
    """Retrieve context from multiple repos and merge results.
    
    This is the 'whole codebase' mode — searches across ALL indexed repos
    and returns the best results regardless of which repo they came from.
    """
    top_k = top_k or settings.RAG_TOP_K
    threshold = threshold or settings.RELEVANCE_THRESHOLD
    
    all_chunks = []
    
    for repo_name in repo_names:
        try:
            chunks = retrieve_context(
                query, repo_name,
                top_k=top_k,
                threshold=threshold,
            )
            all_chunks.extend(chunks)
        except Exception as e:
            print(f"   ⚠️ Error retrieving from {repo_name}: {e}")
    
    # Sort all results by relevance and take top_k
    all_chunks.sort(key=lambda x: x["relevance"], reverse=True)
    return all_chunks[:top_k]


def hybrid_retrieve(
    query: str,
    repo_name: str,
    top_k: int = None,
    semantic_weight: float = 0.7,
    keyword_weight: float = 0.3,
) -> list[dict]:
    """Hybrid search combining semantic + BM25 keyword matching.
    
    Uses reciprocal rank fusion to combine scores.
    """
    top_k = top_k or settings.RAG_TOP_K
    
    # 1. Get semantic results (fetch more for re-ranking)
    semantic_chunks = retrieve_context(query, repo_name, top_k=top_k * 2, threshold=0.1)
    
    if not semantic_chunks:
        return []
    
    # 2. BM25 keyword re-ranking
    try:
        from rank_bm25 import BM25Okapi
        
        corpus = [c["text"].split() for c in semantic_chunks]
        bm25 = BM25Okapi(corpus)
        keyword_scores = bm25.get_scores(query.split())
        
        max_keyword = max(keyword_scores) if max(keyword_scores) > 0 else 1
        
        # 3. Combine scores (reciprocal rank fusion)
        for i, chunk in enumerate(semantic_chunks):
            normalized_keyword = keyword_scores[i] / max_keyword
            chunk["keyword_score"] = round(normalized_keyword, 3)
            chunk["combined_score"] = round(
                chunk["relevance"] * semantic_weight + normalized_keyword * keyword_weight,
                3
            )
        
        # Sort by combined score
        semantic_chunks.sort(key=lambda x: x["combined_score"], reverse=True)
        
    except ImportError:
        # Fall back to semantic-only if rank_bm25 not installed
        for chunk in semantic_chunks:
            chunk["combined_score"] = chunk["relevance"]
    
    return semantic_chunks[:top_k]


def hybrid_retrieve_multi_repo(
    query: str,
    repo_names: list[str],
    top_k: int = None,
    semantic_weight: float = 0.7,
    keyword_weight: float = 0.3,
) -> list[dict]:
    """Hybrid search across multiple repos — the ultimate 'whole codebase' search.
    
    Performs hybrid (semantic + keyword) retrieval on each repo,
    then merges and re-ranks all results.
    """
    top_k = top_k or settings.RAG_TOP_K
    
    all_chunks = []
    
    for repo_name in repo_names:
        try:
            chunks = hybrid_retrieve(
                query, repo_name,
                top_k=top_k,
                semantic_weight=semantic_weight,
                keyword_weight=keyword_weight,
            )
            all_chunks.extend(chunks)
        except Exception as e:
            print(f"   ⚠️ Error in hybrid retrieve from {repo_name}: {e}")
    
    # Re-sort all results by combined score
    all_chunks.sort(key=lambda x: x.get("combined_score", x["relevance"]), reverse=True)
    return all_chunks[:top_k]
