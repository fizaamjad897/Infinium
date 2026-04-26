"""
Unified Query Engine
- Single repo queries
- Multi-repo / whole codebase queries
- Query expansion + hybrid retrieval + generation
- Reasoning trace + conversation history
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
from backend.rag.reasoning import ReasoningTrace, StepTimer
from backend.rag.conversation import format_history_for_prompt, add_turn
from backend.ingestion.embedder import list_collections
from backend.config import settings


def ask(
    query: str,
    repo_name: str,
    use_hybrid: bool = True,
    use_expansion: bool = False,
    use_dual_model: bool = None,
    top_k: int = None,
    model: str = None,
    conversation_id: str = None,
    branch_filter: Optional[str] = None,
    chunk_types: Optional[list] = None,
) -> dict:
    """Ask a question about a single repository.

    Args:
        branch_filter: Scope retrieval to a specific branch name
        chunk_types: Filter to specific chunk types (e.g. ['commit_diff', 'branch'])
    """
    top_k = top_k or settings.RAG_TOP_K

    # Initialize reasoning trace
    trace = ReasoningTrace()
    trace.start(query)

    # Get conversation context for follow-ups
    conversation_context = ""
    if conversation_id:
        conversation_context = format_history_for_prompt(conversation_id)

    # Optionally expand the query
    search_query = query
    if use_expansion:
        with StepTimer(trace, "query_expansion", settings.LLM_MODEL) as timer:
            search_query = expand_query(query)
            timer.input_text = query
            timer.output_text = search_query

    # Retrieve context
    with StepTimer(trace, "retrieval", settings.EMBED_MODEL) as timer:
        if use_hybrid:
            chunks = hybrid_retrieve(
                search_query, repo_name, top_k=top_k,
                chunk_types=chunk_types, branch_filter=branch_filter,
            )
        else:
            chunks = retrieve_context(
                search_query, repo_name, top_k=top_k,
                chunk_types=chunk_types, branch_filter=branch_filter,
            )
        timer.input_text = search_query
        timer.chunks_count = len(chunks)
        timer.output_text = f"Retrieved {len(chunks)} chunks from {repo_name}"

    if not chunks:
        trace.finish()
        return {
            "answer": f"I couldn't find relevant context in the '{repo_name}' repository for your question. "
                      "Make sure the repository has been ingested and try rephrasing your question.",
            "sources": [],
            "model": model or settings.LLM_MODEL,
            "tokens_used": 0,
            "query_used": search_query,
            "chunks_retrieved": 0,
            "reasoning_trace": trace.to_dict(),
        }

    # Generate response
    result = generate_response(
        query, chunks,
        model=model,
        use_dual_model=use_dual_model,
        conversation_context=conversation_context,
        trace=trace,
    )
    result["query_used"] = search_query
    result["chunks_retrieved"] = len(chunks)

    # Save to conversation history
    if conversation_id:
        add_turn(conversation_id, query, result.get("answer", ""), result.get("sources", []))

    return result


def ask_all(
    query: str,
    repo_names: Optional[list[str]] = None,
    use_hybrid: bool = True,
    use_expansion: bool = False,
    use_dual_model: bool = None,
    top_k: int = None,
    model: str = None,
    conversation_id: str = None,
    branch_filter: Optional[str] = None,
    chunk_types: Optional[list] = None,
) -> dict:
    """Ask a question across ALL indexed repositories (whole codebase mode).

    This is the key differentiator — searches across every indexed repo
    and generates a cross-referenced answer with sources from multiple repositories.

    Args:
        query: Natural language question
        repo_names: List of repo names to search (None = all indexed repos)
        use_hybrid: Use hybrid search
        use_expansion: Use query expansion
        use_dual_model: Use dual-model pipeline
        top_k: Number of context chunks to retrieve (across all repos)
        model: LLM model override
        conversation_id: For follow-up questions

    Returns:
        dict with answer, sources (from multiple repos), model, tokens_used, reasoning_trace
    """
    top_k = top_k or settings.RAG_TOP_K

    # Initialize reasoning trace
    trace = ReasoningTrace()
    trace.start(query)

    # Get conversation context
    conversation_context = ""
    if conversation_id:
        conversation_context = format_history_for_prompt(conversation_id)

    # Default to all indexed repos
    if repo_names is None:
        repo_names = list_collections()

    if not repo_names:
        trace.finish()
        return {
            "answer": "No repositories are currently indexed. Please add at least one repository first.",
            "sources": [],
            "model": model or settings.LLM_MODEL,
            "tokens_used": 0,
            "reasoning_trace": trace.to_dict(),
        }

    # Optionally expand the query
    search_query = query
    if use_expansion:
        with StepTimer(trace, "query_expansion", settings.LLM_MODEL) as timer:
            search_query = expand_query(query)
            timer.input_text = query
            timer.output_text = search_query

    # Retrieve across all repos
    with StepTimer(trace, "multi_repo_retrieval", settings.EMBED_MODEL) as timer:
        if use_hybrid:
            chunks = hybrid_retrieve_multi_repo(search_query, repo_names, top_k=top_k)
        else:
            chunks = retrieve_multi_repo(search_query, repo_names, top_k=top_k)
        timer.input_text = f"{search_query} (across {len(repo_names)} repos: {', '.join(repo_names[:5])})"
        timer.chunks_count = len(chunks)
        repos_with_hits = list(set(c.get("repo", "unknown") for c in chunks))
        timer.output_text = f"Retrieved {len(chunks)} chunks from {len(repos_with_hits)} repos"

    if not chunks:
        repos_str = ", ".join(repo_names[:5])
        trace.finish()
        return {
            "answer": f"I couldn't find relevant context across your repositories ({repos_str}) for this question. "
                      "Try rephrasing or check that the relevant repos are indexed.",
            "sources": [],
            "model": model or settings.LLM_MODEL,
            "tokens_used": 0,
            "repos_searched": repo_names,
            "query_used": search_query,
            "reasoning_trace": trace.to_dict(),
        }

    # Generate cross-repo response
    result = generate_response(
        query, chunks,
        model=model,
        use_dual_model=use_dual_model,
        conversation_context=conversation_context,
        trace=trace,
    )
    result["query_used"] = search_query
    result["chunks_retrieved"] = len(chunks)
    result["repos_searched"] = repo_names
    result["repos_with_results"] = list(set(c.get("repo", "unknown") for c in chunks))

    # Save to conversation history
    if conversation_id:
        add_turn(conversation_id, query, result.get("answer", ""), result.get("sources", []))

    return result
