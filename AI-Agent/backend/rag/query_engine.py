"""
Unified Query Engine
- Single repo queries
- Multi-repo / whole codebase queries
- Query expansion + hybrid retrieval + generation
"""
from typing import Optional
from backend.rag.retriever import (
    retrieve_context,
    retrieve_multi_repo,
    hybrid_retrieve,
    hybrid_retrieve_multi_repo,
)
from backend.rag.reranker import expand_query
from backend.rag.generator import generate_response
from backend.ingestion.embedder import list_collections
from backend.config import settings


def ask(
    query: str,
    repo_name: str,
    use_hybrid: bool = True,
    use_expansion: bool = False,
    top_k: int = None,
    model: str = None,
) -> dict:
    """Ask a question about a single repository.
    
    Args:
        query: Natural language question
        repo_name: Name of the repo to search
        use_hybrid: Use hybrid (semantic + BM25) search
        use_expansion: Use LLM to expand the query first
        top_k: Number of context chunks to retrieve
        model: LLM model override
    
    Returns:
        dict with answer, sources, model, tokens_used
    """
    top_k = top_k or settings.RAG_TOP_K
    
    # Optionally expand the query
    search_query = query
    if use_expansion:
        search_query = expand_query(query)
    
    # Retrieve context
    if use_hybrid:
        chunks = hybrid_retrieve(search_query, repo_name, top_k=top_k)
    else:
        chunks = retrieve_context(search_query, repo_name, top_k=top_k)
    
    if not chunks:
        return {
            "answer": f"I couldn't find relevant context in the '{repo_name}' repository for your question. "
                      "Make sure the repository has been ingested and try rephrasing your question.",
            "sources": [],
            "model": model or settings.LLM_MODEL,
            "tokens_used": 0,
            "query_used": search_query,
        }
    
    # Generate response
    result = generate_response(query, chunks, model=model)
    result["query_used"] = search_query
    result["chunks_retrieved"] = len(chunks)
    return result


def ask_all(
    query: str,
    repo_names: Optional[list[str]] = None,
    use_hybrid: bool = True,
    use_expansion: bool = False,
    top_k: int = None,
    model: str = None,
) -> dict:
    """Ask a question across ALL indexed repositories (whole codebase mode).
    
    This is the key differentiator — like Copilot but not limited to one repo.
    Searches across every indexed repo and generates a cross-referenced answer.
    
    Args:
        query: Natural language question
        repo_names: List of repo names to search (None = all indexed repos)
        use_hybrid: Use hybrid search
        use_expansion: Use query expansion
        top_k: Number of context chunks to retrieve (across all repos)
        model: LLM model override
    
    Returns:
        dict with answer, sources (from multiple repos), model, tokens_used
    """
    top_k = top_k or settings.RAG_TOP_K
    
    # Default to all indexed repos
    if repo_names is None:
        repo_names = list_collections()
    
    if not repo_names:
        return {
            "answer": "No repositories are currently indexed. Please add at least one repository first.",
            "sources": [],
            "model": model or settings.LLM_MODEL,
            "tokens_used": 0,
        }
    
    # Optionally expand the query
    search_query = query
    if use_expansion:
        search_query = expand_query(query)
    
    # Retrieve across all repos
    if use_hybrid:
        chunks = hybrid_retrieve_multi_repo(search_query, repo_names, top_k=top_k)
    else:
        chunks = retrieve_multi_repo(search_query, repo_names, top_k=top_k)
    
    if not chunks:
        repos_str = ", ".join(repo_names[:5])
        return {
            "answer": f"I couldn't find relevant context across your repositories ({repos_str}) for this question. "
                      "Try rephrasing or check that the relevant repos are indexed.",
            "sources": [],
            "model": model or settings.LLM_MODEL,
            "tokens_used": 0,
            "repos_searched": repo_names,
            "query_used": search_query,
        }
    
    # Generate cross-repo response
    result = generate_response(query, chunks, model=model)
    result["query_used"] = search_query
    result["chunks_retrieved"] = len(chunks)
    result["repos_searched"] = repo_names
    result["repos_with_results"] = list(set(c.get("repo", "unknown") for c in chunks))
    return result
