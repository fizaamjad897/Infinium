"""
Git History API Routes
Exposes indexed git data directly — branches, commits, and dedicated git reasoning queries.

GET  /api/repos/{repo_name}/branches   → list all indexed branches with metadata
GET  /api/repos/{repo_name}/commits    → list indexed commits (filterable by branch)
POST /api/query/git                    → RAG query scoped to git history only
"""
from fastapi import APIRouter, HTTPException, Query
from typing import Optional

from backend.api.models import (
    GitQueryRequest, GitHistoryResponse, BranchInfo, CommitInfo, QueryResponse,
)
from backend.ingestion.embedder import get_chroma_collection

router = APIRouter(prefix="/api", tags=["Git History"])


def _get_git_chunks(repo_name: str, chunk_type: str, branch_filter: Optional[str] = None) -> list[dict]:
    """Fetch all indexed chunks of a given type directly from ChromaDB."""
    try:
        collection = get_chroma_collection(repo_name)
        if collection.count() == 0:
            return []

        where: dict = {"type": {"$eq": chunk_type}}
        if branch_filter:
            where = {"$and": [{"type": {"$eq": chunk_type}}, {"branch": {"$eq": branch_filter}}]}

        results = collection.get(
            where=where,
            include=["documents", "metadatas"],
            limit=2000,
        )
        chunks = []
        for doc, meta in zip(results["documents"], results["metadatas"]):
            chunks.append({"text": doc, "meta": meta})
        return chunks
    except Exception as e:
        print(f"   ⚠️ Error fetching {chunk_type} chunks from {repo_name}: {e}")
        return []


@router.get("/repos/{repo_name}/branches", response_model=GitHistoryResponse)
def get_repo_branches(repo_name: str):
    """List all indexed branches for a repository.

    Returns branch names, HEAD commit SHA, author, date, and whether the branch
    has been merged. Use this to understand what's being worked on across branches.

    Query examples for /api/query with branch_filter:
      - "What features were added on this branch?"
      - "How does this branch differ from the code we saw before?"
    """
    branch_chunks = _get_git_chunks(repo_name, "branch")
    if not branch_chunks:
        raise HTTPException(status_code=404, detail=f"No branch data found for '{repo_name}'. Re-ingest the repo to index branches.")

    branches = []
    for c in branch_chunks:
        m = c["meta"]
        branches.append(BranchInfo(
            name=m.get("branch_name", m.get("source", "unknown")),
            last_commit_sha=m.get("last_commit_sha", ""),
            last_commit_author=m.get("last_commit_author", ""),
            last_commit_date=m.get("last_commit_date", ""),
            last_commit_message=m.get("last_commit_message", ""),
            is_default=bool(m.get("is_default", False)),
            merged=bool(m.get("merged", False)),
        ))

    # Sort: default branch first, then by name
    branches.sort(key=lambda b: (not b.is_default, b.name))

    return GitHistoryResponse(
        repo_name=repo_name,
        branches=branches,
        total_branches=len(branches),
    )


@router.get("/repos/{repo_name}/commits", response_model=GitHistoryResponse)
def get_repo_commits(
    repo_name: str,
    branch: Optional[str] = Query(None, description="Filter commits by branch name"),
    limit: int = Query(50, ge=1, le=500, description="Max commits to return"),
):
    """List indexed commits for a repository.

    Returns SHA, message, author, date, and files changed.
    Filter by branch to see what changed on a specific branch.

    Use ?branch=feature-auth to scope to one branch.
    Use ?limit=100 for more history.
    """
    commit_chunks = _get_git_chunks(repo_name, "commit", branch_filter=branch)
    diff_chunks = _get_git_chunks(repo_name, "commit_diff", branch_filter=branch)

    # Build a set of SHAs that have diffs indexed
    shas_with_diffs = {c["meta"].get("sha", "") for c in diff_chunks}

    commits = []
    seen_shas = set()
    for c in commit_chunks:
        m = c["meta"]
        sha = m.get("sha", "")
        if sha in seen_shas:
            continue
        seen_shas.add(sha)

        files = m.get("files_changed", "")
        if isinstance(files, str):
            files = [f.strip() for f in files.split(",") if f.strip()]

        commits.append(CommitInfo(
            sha=sha,
            message=m.get("message", ""),
            author=m.get("author", ""),
            date=m.get("date", ""),
            branch=m.get("branch", branch or ""),
            files_changed=files,
            has_diff=sha in shas_with_diffs,
        ))

    # Sort newest first
    commits.sort(key=lambda c: c.date, reverse=True)
    commits = commits[:limit]

    return GitHistoryResponse(
        repo_name=repo_name,
        commits=commits,
        total_commits=len(commits),
    )


@router.post("/query/git", response_model=QueryResponse)
def query_git_history(req: GitQueryRequest):
    """Dedicated git history reasoning endpoint.

    Searches ONLY commit messages, commit diffs, and branch metadata —
    not source files. The LLM reasons specifically on what changed,
    when, and why based on git history.

    Examples:
      - "What features were added on the feature-auth branch?"
      - "Which commits changed the authentication logic?"
      - "What was worked on most recently in main?"
      - "How does feature-login differ from main in terms of commits?"
      - "Who made the most changes in the last 10 commits?"

    Set branch_filter to scope to a specific branch.
    """
    from backend.rag.query_engine import ask

    # Build chunk_types from include flags
    chunk_types = []
    if req.include_diffs:
        chunk_types.append("commit_diff")
    if req.include_commits:
        chunk_types.append("commit")
    if req.include_branches:
        chunk_types.append("branch")

    if not chunk_types:
        chunk_types = ["commit_diff", "commit", "branch"]

    # Prepend a git-context hint to the query so the LLM focuses on history
    branch_hint = f" Focus specifically on the '{req.branch_filter}' branch." if req.branch_filter else ""
    enriched_query = f"{req.query}{branch_hint}"

    result = ask(
        query=enriched_query,
        repo_name=req.repo_name,
        use_hybrid=True,
        use_expansion=False,
        use_dual_model=req.use_dual_model,
        top_k=req.top_k,
        chunk_types=chunk_types,
        branch_filter=req.branch_filter,
    )
    return result
