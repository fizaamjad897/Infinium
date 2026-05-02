"""
Remote URL Ingestion Route
POST /api/ingest/url — Index any public GitHub repo without cloning.
"""
from __future__ import annotations
from typing import Optional
from fastapi import APIRouter, BackgroundTasks, Depends
from backend.api.models import RemoteIngestRequest, IngestResponse
from backend.ingestion.pipeline import ingest_remote_url
from backend.auth.jwt_handler import get_current_user_optional

router = APIRouter(prefix="/api/ingest", tags=["Ingestion"])


@router.post("/url", response_model=IngestResponse)
async def ingest_remote_repo(
    request: RemoteIngestRequest,
    background_tasks: BackgroundTasks,
    current_user: Optional[dict] = Depends(get_current_user_optional),
):
    """Index any public GitHub repository without cloning it.

    Uses the GitHub Contents API to stream file contents, commits,
    and branches directly. No git clone required.

    For private repos or to avoid rate limits (60/hr unauthenticated
    vs 5000/hr authenticated), pass your github_token.

    Examples:
    - https://github.com/tiangolo/fastapi
    - https://github.com/psf/requests
    - https://github.com/your-org/your-public-repo
    """
    background_tasks.add_task(
        ingest_remote_url,
        repo_url=request.repo_url,
        repo_name=request.repo_name,
        branch=request.branch,
        github_token=request.github_token,
        ingested_by=(current_user.get("login", "") if current_user else ""),
    )

    return IngestResponse(
        status="ingestion_started",
        repo_name=request.repo_name,
        message=(
            f"Remote ingestion started for: {request.repo_url} "
            f"(branch: {request.branch or 'default'}). "
            f"Check /api/repos/{request.repo_name}/status for progress."
        ),
    )
