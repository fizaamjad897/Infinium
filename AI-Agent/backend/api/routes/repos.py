"""
Repository Management Routes
- List indexed repos
- Get repo status
- Remove repos
- Trigger re-indexing
"""
from typing import Optional
from fastapi import APIRouter, BackgroundTasks, HTTPException, Depends, Header
from backend.ingestion.pipeline import (
    get_all_repos,
    get_ingestion_status,
    remove_repository,
    reindex_repository,
)
from backend.ingestion.embedder import get_collection_stats, list_collections
from backend.auth.jwt_handler import get_current_user, get_current_user_optional
from backend.auth.models import UserStore

router = APIRouter(prefix="/api/repos", tags=["Repositories"])
user_store = UserStore()


@router.get("/")
async def list_repos():
    """List all indexed repositories with their status."""
    repos = get_all_repos()
    
    # Enrich with ChromaDB stats
    for repo in repos:
        stats = get_collection_stats(repo["repo_name"])
        repo["chunks"] = stats.get("chunks", 0)
    
    return repos


@router.get("/{repo_name}/status")
async def repo_status(repo_name: str):
    """Get detailed status for a specific repository."""
    status = get_ingestion_status(repo_name)
    stats = get_collection_stats(repo_name)
    
    return {
        **status,
        "repo_name": repo_name,
        "chunks_in_db": stats.get("chunks", 0),
    }


@router.delete("/{repo_name}")
async def delete_repo(
    repo_name: str,
    x_github_token: Optional[str] = Header(None),  # Node-backend bypass
    current_user: Optional[dict] = Depends(get_current_user_optional),
):
    """Remove a repository and all its indexed data.

    Authorised via either an ``X-GitHub-Token`` header (Node.js backend has
    already authenticated the user) or a JWT bearer token.
    """
    if not (x_github_token or current_user):
        raise HTTPException(status_code=401, detail="Auth required (X-GitHub-Token header or JWT)")
    remove_repository(repo_name)
    return {"status": "removed", "repo_name": repo_name}


@router.post("/{repo_name}/reindex")
async def trigger_reindex(
    repo_name: str,
    background_tasks: BackgroundTasks,
    x_github_token: Optional[str] = Header(None),  # Node-backend forwarded
    current_user: Optional[dict] = Depends(get_current_user_optional),
):
    """Trigger re-indexing of a repository (delete old data + re-ingest)."""
    if not (x_github_token or current_user):
        raise HTTPException(status_code=401, detail="Auth required (X-GitHub-Token header or JWT)")

    # Token resolution: Node-supplied wins, then OAuth fallback
    github_token = x_github_token
    if not github_token and current_user:
        user = user_store.get_user(current_user["github_id"])
        github_token = user.get("access_token") if user else None

    background_tasks.add_task(
        reindex_repository,
        repo_name,
        github_token=github_token,
    )

    return {
        "status": "reindex_started",
        "repo_name": repo_name,
        "message": f"Re-indexing started. Check /api/repos/{repo_name}/status for progress.",
    }


@router.get("/collections/list")
async def list_all_collections():
    """List all ChromaDB collections (raw collection names)."""
    return {"collections": list_collections()}
