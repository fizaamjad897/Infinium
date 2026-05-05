"""
Ingestion API Routes

  POST /api/ingest/repo      → Combined private/public repo ingestion
  POST /api/ingest/branches  → Ingest all branches of a repo (faster embed model)
  POST /api/ingest/private   → Clone-based ingestion of PRIVATE repos (auth required)
  POST /api/ingest/local     → Ingest a local folder (no GitHub round-trip)
  POST /api/webhook/github   → Incremental sync from GitHub webhooks (FR-005)

Public-repo ingestion also available at /api/ingest/public (routes/remote.py).
"""
import os
import re
import hmac
import hashlib
import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Header, Depends
from typing import Optional

from backend.api.models import IngestRequest, IngestLocalRequest, IngestResponse
from backend.ingestion.pipeline import (
    ingest_repository,
    ingest_local_folder,
)
from backend.auth.jwt_handler import get_current_user_optional
from backend.auth.models import UserStore
from backend.config import settings

router = APIRouter(prefix="/api", tags=["Ingestion"])
user_store = UserStore()


_GITHUB_URL_RE = re.compile(r"^https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$")


async def _is_repo_private(repo_url: str, token: Optional[str]) -> Optional[bool]:
    """Return True/False/None — None means 'could not determine' (network/404)."""
    m = _GITHUB_URL_RE.match(repo_url.strip())
    if not m:
        return None
    owner, repo = m.group(1), m.group(2)
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"https://api.github.com/repos/{owner}/{repo}",
                                    headers=headers, timeout=8)
        if resp.status_code != 200:
            return None
        return bool(resp.json().get("private", False))
    except Exception:
        return None


# ── Private-repo ingestion (auth required) ──

# ── Unified repo ingestion (private/public) ──

@router.post("/ingest/repo", response_model=IngestResponse)
async def ingest_repo(
    req: IngestRequest,
    background_tasks: BackgroundTasks,
    x_github_token: Optional[str] = Header(None),
    x_user_login: Optional[str] = Header(None),
    current_user: Optional[dict] = Depends(get_current_user_optional),
):
    """Ingest a GitHub repository (private or public).
    
    If no branch is specified, it will default to the repository's primary branch (main/master).
    For private repos, authentication is required via X-GitHub-Token header or session.
    """
    github_token = x_github_token
    user_login = x_user_login or ""
    
    if not github_token and current_user:
        user = user_store.get_user(current_user["github_id"])
        github_token = user.get("access_token") if user else None
        user_login = user_login or current_user.get("login", "")

    # Detect visibility
    is_private = await _is_repo_private(req.repo_url, github_token)
    
    if is_private:
        if not github_token:
            raise HTTPException(
                status_code=401,
                detail="Private repository detected. Authentication required. Supply 'X-GitHub-Token' header."
            )
        print(f"   🔒 Private repo detected for {req.repo_name}")
        # Use clone-based ingestion for private repos
        background_tasks.add_task(
            ingest_repository,
            repo_url=req.repo_url,
            repo_name=req.repo_name,
            branch=req.branch,
            github_token=github_token,
            ingested_by=user_login,
            callback_url=req.callback_url or "",
            status_scope="repo",
            embed_model=settings.EMBED_MODEL,
        )
    else:
        print(f"   🌍 Public repo detected for {req.repo_name}")
        # Use remote API ingestion for public repos
        from backend.ingestion.pipeline import ingest_remote_url
        background_tasks.add_task(
            ingest_remote_url,
            repo_url=req.repo_url,
            repo_name=req.repo_name,
            branch=req.branch,
            branches=req.branches,
            github_token=github_token or settings.GITHUB_API_TOKEN,
            ingested_by=user_login,
            callback_url=req.callback_url or "",
            status_scope="repo",
            embed_model=settings.EMBED_MODEL,
        )

    branch_msg = f"branch '{req.branch}'" if req.branch else "default branch"
    return IngestResponse(
        status="ingestion_started",
        repo_name=req.repo_name,
        message=f"Background ingestion started for {req.repo_name} ({branch_msg}).",
    )

# Route moved and unified above

# ── All branches ingestion ──

@router.post("/ingest/branches", response_model=IngestResponse)
async def ingest_all_branches(
    req: IngestRequest,
    background_tasks: BackgroundTasks,
    x_github_token: Optional[str] = Header(None),
    x_user_login: Optional[str] = Header(None),
    current_user: Optional[dict] = Depends(get_current_user_optional),
):
    """Ingest all branches of a GitHub repository.
    
    This endpoint always indexes every branch it can fetch for the repo.
    Any branch value supplied in the payload is ignored.
    It works for both public and private repos.
    Status is tracked separately as 'branches_ingestion'.
    """
    github_token = x_github_token
    user_login = x_user_login or ""
    
    if not github_token and current_user:
        user = user_store.get_user(current_user["github_id"])
        github_token = user.get("access_token") if user else None
        user_login = user_login or current_user.get("login", "")

    # Check if repo is private
    is_private = await _is_repo_private(req.repo_url, github_token)
    
    if is_private:
        if not github_token:
            raise HTTPException(
                status_code=401,
                detail="Private repository detected. Authentication required. Supply 'X-GitHub-Token' header."
            )
        print(f"   🔒 Private repo detected for {req.repo_name} — ingesting all branches")
    else:
        print(f"   🌍 Public repo detected for {req.repo_name} — ingesting all branches")

    # Fetch all branches (branch in payload is ignored)
    from backend.ingestion.github_loader import fetch_all_remote_branches, list_all_branch_names
    import os
    dest = os.path.join(settings.REPO_CACHE_DIR, req.repo_name.replace("/", "_"))
    
    # Clone repo to get all branches
    from backend.ingestion.github_loader import clone_repo
    path = clone_repo(req.repo_url, dest, github_token=github_token)
    fetch_all_remote_branches(path)
    all_branches = list_all_branch_names(path)
    
    print(f"   🌿 Found {len(all_branches)} branches: {all_branches[:10]}{'...' if len(all_branches) > 10 else ''}")

    # Use clone-based ingestion with all branches and the faster embed model
    background_tasks.add_task(
        ingest_repository,
        repo_url=req.repo_url,
        repo_name=req.repo_name,
        branches=all_branches,
        github_token=github_token,
        ingested_by=user_login,
        callback_url=req.callback_url or "",
        embed_model=settings.BRANCH_EMBED_MODEL,
        status_scope="branches",
    )

    return IngestResponse(
        status="branches_ingestion_started",
        repo_name=req.repo_name,
        message=f"Background ingestion started for all {len(all_branches)} branches of {req.repo_name} using {settings.BRANCH_EMBED_MODEL}.",
    )

# ── Local-folder ingestion ──

@router.post("/ingest/local", response_model=IngestResponse)
async def ingest_local(
    req: IngestLocalRequest,
    background_tasks: BackgroundTasks,
    current_user: Optional[dict] = Depends(get_current_user_optional),
):
    """Ingest a local folder. No GitHub round-trip — useful for testing or
    repos already cloned on disk."""
    if not os.path.exists(req.folder_path):
        raise HTTPException(status_code=404, detail=f"Folder not found: {req.folder_path}")

    background_tasks.add_task(
        ingest_local_folder,
        req.folder_path,
        req.repo_name,
        ingested_by=(current_user.get("login", "") if current_user else ""),
        callback_url=req.callback_url or "",
        branch=req.branch,
        branches=req.branches,
    )

    return IngestResponse(
        status="ingestion_started",
        repo_name=req.repo_name,
        message=f"Background ingestion started for local folder: {req.folder_path}",
    )


# ── GitHub webhook (FR-005) ──

@router.post("/webhook/github")
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: Optional[str] = Header(None),
):
    """GitHub webhook receiver for incremental sync.

    Register at: GitHub repo → Settings → Webhooks →
    Payload URL ``https://<host>/api/webhook/github``, secret =
    ``GITHUB_WEBHOOK_SECRET``.
    """
    body = await request.body()

    if settings.GITHUB_WEBHOOK_SECRET:
        secret = settings.GITHUB_WEBHOOK_SECRET.encode()
        expected = "sha256=" + hmac.new(key=secret, msg=body, digestmod=hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, x_hub_signature_256 or ""):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    payload = await request.json()
    event = request.headers.get("x-github-event", "")

    if event in ("push", "pull_request", "issues"):
        repo_full_name = payload.get("repository", {}).get("full_name", "")
        repo_clone_url = payload.get("repository", {}).get("clone_url", "")
        is_private = bool(payload.get("repository", {}).get("private", False))
        repo_name = repo_full_name.replace("/", "_")

        if repo_clone_url:
            # For private repos triggered by a webhook, fall back to server-level
            # token if available — webhook payloads don't include user tokens.
            token = settings.GITHUB_API_TOKEN if is_private else None
            background_tasks.add_task(
                ingest_repository,
                repo_clone_url,
                repo_name,
                github_token=token,
            )
            return {"status": "sync_triggered", "event": event, "repo": repo_name}

    return {"status": "ignored", "event": event}
