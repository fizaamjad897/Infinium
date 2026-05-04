"""
Repository Management Routes
- List indexed repos
- Get repo status (snapshot + SSE stream for live progress)
- Pre-ingest size estimate
- Remove repos
- Trigger re-indexing
"""
import asyncio
import json
import re
import time
from typing import Optional
import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Depends, Header
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.ingestion.pipeline import (
    get_all_repos,
    get_ingestion_status,
    remove_repository,
    reindex_repository,
)
from backend.ingestion.embedder import get_collection_stats, list_collections
from backend.auth.jwt_handler import get_current_user, get_current_user_optional
from backend.auth.models import UserStore
from backend.config import settings

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


# ── SSE: live status stream ──

@router.get("/{repo_name}/status/stream")
async def stream_status(repo_name: str):
    """Server-Sent Events stream of ingestion status updates.

    Emits one JSON event per second until the status reaches ``complete`` or
    ``error``, plus an immediate first event so the client gets a baseline.

    Drop-in for the Node.js backend / dashboard:
    ```js
    const es = new EventSource('/api/repos/my-repo/status/stream');
    es.onmessage = e => console.log(JSON.parse(e.data));
    es.addEventListener('done', () => es.close());
    ```
    """
    async def event_gen():
        last_payload: str = ""
        terminal_states = {"complete", "error", "unknown"}
        # Hard cap so an SSE client can't park forever
        deadline = time.time() + 60 * 60   # 1 hour
        while time.time() < deadline:
            status = get_ingestion_status(repo_name)
            stats = get_collection_stats(repo_name)
            payload = {
                **status,
                "repo_name": repo_name,
                "chunks_in_db": stats.get("chunks", 0),
            }
            text = json.dumps(payload, default=str)
            # Only emit when something changed (saves bandwidth)
            if text != last_payload:
                yield f"data: {text}\n\n"
                last_payload = text
            if status.get("status") in terminal_states:
                yield "event: done\ndata: {}\n\n"
                return
            await asyncio.sleep(1.0)
        yield "event: timeout\ndata: {\"reason\":\"deadline_exceeded\"}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # tell nginx not to buffer
            "Connection": "keep-alive",
        },
    )


# ── Pre-ingest size estimate ──

class EstimateRequest(BaseModel):
    repo_url: str = Field(..., description="GitHub URL — public or private")
    branch: Optional[str] = Field(None, description="Branch to estimate against (default: repo default)")
    github_token: Optional[str] = Field(None, description="Token for private repos / higher rate limit")


_GITHUB_URL_RE = re.compile(r"^https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$")


@router.post("/estimate")
async def estimate_ingest(req: EstimateRequest):
    """Estimate how big and how long an ingestion will take.

    Calls a few cheap GitHub API endpoints (``/repos``, ``/git/trees``,
    ``/commits``) and returns a rough estimate. Use this from the Node.js
    backend to warn the user before they kick off a 30-minute job.
    """
    m = _GITHUB_URL_RE.match(req.repo_url.strip())
    if not m:
        raise HTTPException(status_code=400, detail="Invalid GitHub URL")
    owner, repo = m.group(1), m.group(2)

    token = req.github_token or settings.GITHUB_API_TOKEN or None
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    async with httpx.AsyncClient() as client:
        info_r = await client.get(f"https://api.github.com/repos/{owner}/{repo}",
                                  headers=headers, timeout=10)
        if info_r.status_code != 200:
            raise HTTPException(status_code=info_r.status_code,
                                detail=f"GitHub API error: {info_r.text[:200]}")
        info = info_r.json()
        branch = req.branch or info.get("default_branch", "main")

        # File count via the recursive tree endpoint
        tree_r = await client.get(
            f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1",
            headers=headers, timeout=15,
        )
        files = []
        if tree_r.status_code == 200:
            tree_data = tree_r.json()
            files = [t for t in tree_data.get("tree", []) if t.get("type") == "blob"]

        # First page of commits to gauge total via Link header parsing
        commits_r = await client.get(
            f"https://api.github.com/repos/{owner}/{repo}/commits?sha={branch}&per_page=1",
            headers=headers, timeout=10,
        )
        commit_count_estimate = 0
        if commits_r.status_code == 200:
            link = commits_r.headers.get("link", "")
            last_match = re.search(r'<[^>]*[?&]page=(\d+)[^>]*>;\s*rel="last"', link)
            if last_match:
                commit_count_estimate = int(last_match.group(1))
            else:
                # fewer than 1 page → either 0 or 1
                commit_count_estimate = len(commits_r.json() or [])

    # Heuristic: ~3 chunks per file (avg) + 1 chunk per commit; embedding ~5 chunks/sec
    # parallel @ 4 workers → ~5/s aggregate. Tunable in ``EMBED_WORKERS``.
    capped_commits = min(commit_count_estimate, settings.MAX_COMMITS)
    estimated_chunks = (len(files) * 3) + capped_commits + 10  # +10 for branches headroom
    eta_seconds = int(estimated_chunks / 5)
    eta_minutes = round(eta_seconds / 60, 1)

    return {
        "repo_full_name": info.get("full_name", ""),
        "default_branch": info.get("default_branch", ""),
        "indexing_branch": branch,
        "is_private": bool(info.get("private", False)),
        "size_kb": info.get("size", 0),                # GitHub's repo size in KB
        "files_total": len(files),
        "commits_total_on_branch": commit_count_estimate,
        "commits_to_index": capped_commits,
        "estimated_chunks": estimated_chunks,
        "estimated_seconds": eta_seconds,
        "estimated_minutes": eta_minutes,
        "verdict": (
            "small" if eta_seconds < 60 else
            "medium" if eta_seconds < 600 else
            "large" if eta_seconds < 1800 else
            "huge"
        ),
        "recommendation": (
            "Use a callback_url or SSE stream — synchronous waiting will time out."
            if eta_seconds > 60 else
            "Fast — synchronous polling is fine."
        ),
    }
