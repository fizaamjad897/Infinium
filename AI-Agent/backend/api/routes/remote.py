"""
Public-Repo Ingestion (FR-003 / FR-004)

  POST /api/ingest/public  → Index any PUBLIC GitHub repo via the GitHub REST
                              API. No clone, no auth required.

The endpoint enforces that the URL really is public — if it's private, the
request is rejected with a clear hint to use /api/ingest/private instead.
"""
from __future__ import annotations
import re
import httpx
from typing import Optional
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from backend.api.models import RemoteIngestRequest, IngestResponse
from backend.ingestion.pipeline import ingest_remote_url
from backend.auth.jwt_handler import get_current_user_optional
from backend.config import settings

router = APIRouter(prefix="/api/ingest", tags=["Ingestion"])


_GITHUB_URL_RE = re.compile(r"^https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$")


async def _confirm_public(repo_url: str) -> tuple[bool, Optional[str]]:
    """Return (is_public, error_or_full_name). Uses server-level token only
    to avoid the 60/hr anonymous rate limit; never accepts user tokens here."""
    m = _GITHUB_URL_RE.match(repo_url.strip())
    if not m:
        return False, f"Invalid GitHub URL: {repo_url}"
    owner, repo = m.group(1), m.group(2)

    headers = {"Accept": "application/vnd.github+json"}
    if settings.GITHUB_API_TOKEN:
        headers["Authorization"] = f"Bearer {settings.GITHUB_API_TOKEN}"

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"https://api.github.com/repos/{owner}/{repo}",
                                    headers=headers, timeout=8)
    except Exception as e:
        return False, f"Could not reach GitHub: {e}"

    if resp.status_code == 404:
        return False, "Repository not found. If it's private, use /api/ingest/private instead."
    if resp.status_code != 200:
        return False, f"GitHub API error {resp.status_code}: {resp.text[:200]}"

    data = resp.json()
    if data.get("private", False):
        return False, (
            f"'{data.get('full_name','')}' is a PRIVATE repository. "
            "Use /api/ingest/private for private repos (auth required)."
        )
    return True, data.get("full_name", "")


@router.post("/public", response_model=IngestResponse)
async def ingest_public_repo(
    request: RemoteIngestRequest,
    background_tasks: BackgroundTasks,
    current_user: Optional[dict] = Depends(get_current_user_optional),
):
    """Index a PUBLIC GitHub repository via the GitHub REST API (no clone).

    No auth required. The server's ``GITHUB_API_TOKEN`` is used purely to lift
    the GitHub rate limit from 60/hr → 5000/hr — callers do not pass a token.

    Examples:
      - https://github.com/tiangolo/fastapi
      - https://github.com/psf/requests
      - any other public repo URL
    """
    is_public, info = await _confirm_public(request.repo_url)
    if not is_public:
        raise HTTPException(status_code=400, detail=info)

    background_tasks.add_task(
        ingest_remote_url,
        repo_url=request.repo_url,
        repo_name=request.repo_name,
        branch=request.branch,
        github_token=settings.GITHUB_API_TOKEN or None,   # server-level only
        ingested_by=(current_user.get("login", "") if current_user else ""),
        callback_url=request.callback_url or "",
    )

    return IngestResponse(
        status="ingestion_started",
        repo_name=request.repo_name,
        message=(
            f"Public ingestion started for {info or request.repo_url} "
            f"(branch: {request.branch or 'default'}). "
            f"Check /api/repos/{request.repo_name}/status for progress."
        ),
    )
