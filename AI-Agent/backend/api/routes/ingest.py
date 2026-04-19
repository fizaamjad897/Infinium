"""
Ingestion API Routes
- Add GitHub repos (with auth token for private repos)
- Ingest local folders
- GitHub webhook for incremental sync
"""
import os
import hmac
import hashlib
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Header, Depends
from typing import Optional

from backend.api.models import IngestRequest, IngestLocalRequest, IngestResponse
from backend.ingestion.pipeline import (
    ingest_repository,
    ingest_local_folder,
    get_ingestion_status,
)
from backend.auth.jwt_handler import get_current_user
from backend.auth.models import UserStore
from backend.config import settings

router = APIRouter(prefix="/api", tags=["Ingestion"])
user_store = UserStore()


@router.post("/ingest", response_model=IngestResponse)
async def ingest_github_repo(
    req: IngestRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
):
    """Start ingesting a GitHub repository (runs as background task).
    
    Uses the authenticated user's GitHub token for private repo access.
    """
    # Get user's GitHub token for private repo access
    user = user_store.get_user(current_user["github_id"])
    github_token = user.get("access_token") if user else None
    
    background_tasks.add_task(
        ingest_repository,
        req.repo_url,
        req.repo_name,
        github_token=github_token,
    )
    
    return IngestResponse(
        status="ingestion_started",
        repo_name=req.repo_name,
        message=f"Background ingestion started for {req.repo_name}. Check /api/repos/{req.repo_name}/status for progress.",
    )


@router.post("/ingest/local", response_model=IngestResponse)
async def ingest_local(
    req: IngestLocalRequest,
    background_tasks: BackgroundTasks,
):
    """Ingest a local folder (no auth required — for dev/testing).
    
    This endpoint is useful for testing or for repos already cloned on disk.
    """
    if not os.path.exists(req.folder_path):
        raise HTTPException(status_code=404, detail=f"Folder not found: {req.folder_path}")
    
    background_tasks.add_task(
        ingest_local_folder,
        req.folder_path,
        req.repo_name,
    )
    
    return IngestResponse(
        status="ingestion_started",
        repo_name=req.repo_name,
        message=f"Background ingestion started for local folder: {req.folder_path}",
    )


@router.post("/webhook/github")
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: Optional[str] = Header(None),
):
    """GitHub webhook receiver for incremental sync.
    
    Register this URL (https://your-domain/api/webhook/github) in your
    GitHub repo's Settings → Webhooks.
    """
    body = await request.body()
    
    # Verify webhook signature if secret is configured
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
        repo_name = repo_full_name.replace("/", "_")
        
        if repo_clone_url:
            background_tasks.add_task(
                ingest_repository,
                repo_clone_url,
                repo_name,
            )
            return {"status": "sync_triggered", "event": event, "repo": repo_name}
    
    return {"status": "ignored", "event": event}
