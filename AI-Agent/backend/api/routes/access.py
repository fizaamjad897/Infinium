"""
Repository Access Validation Routes (FR-002, FR-003)

POST /api/access/validate
    - Verify user has at least read access to a repository before adding.
    - Public repos pass automatically (FR-003).
    - Private repos: query GitHub /repos/{owner}/{repo} with user's stored token (FR-002).
"""
from __future__ import annotations
import re
import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

from backend.auth.jwt_handler import get_current_user
from backend.auth.models import UserStore
from backend.config import settings

router = APIRouter(prefix="/api/access", tags=["Access Control"])
user_store = UserStore()


class AccessValidationRequest(BaseModel):
    repo_url: str = Field(..., description="GitHub repository URL")
    github_token: Optional[str] = Field(
        None,
        description=(
            "Optional GitHub PAT. If omitted, the authenticated user's stored "
            "OAuth token is used."
        ),
    )


class AccessValidationResponse(BaseModel):
    allowed: bool
    is_public: bool
    repo_full_name: str = ""
    permission: str = ""           # admin / write / read / none
    default_branch: str = ""
    private: bool = False
    reason: str = ""


_GITHUB_URL_RE = re.compile(
    r"^https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$"
)


def _parse(repo_url: str) -> tuple[str, str]:
    m = _GITHUB_URL_RE.match(repo_url.strip())
    if not m:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid GitHub URL: {repo_url}. Expected https://github.com/owner/repo",
        )
    return m.group(1), m.group(2)


@router.post("/validate", response_model=AccessValidationResponse)
async def validate_access(
    req: AccessValidationRequest,
    current_user: Optional[dict] = Depends(get_current_user),
):
    """Pre-flight check before ingesting a repo.

    - Public repos return ``allowed=True, is_public=True`` without needing a token.
    - Private repos require either ``github_token`` in the request OR a logged-in
      user whose stored OAuth token has access.
    """
    owner, repo = _parse(req.repo_url)

    # Resolve token: explicit param wins, then user's stored OAuth token,
    # then the server-level GITHUB_API_TOKEN as a last resort.
    token = req.github_token
    if not token and current_user:
        user = user_store.get_user(current_user["github_id"])
        token = user.get("access_token") if user else None
    if not token:
        token = settings.GITHUB_API_TOKEN or None

    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    api_url = f"https://api.github.com/repos/{owner}/{repo}"
    async with httpx.AsyncClient() as client:
        resp = await client.get(api_url, headers=headers, timeout=10)

    if resp.status_code == 404:
        # Could be private + no access, or genuinely missing.
        return AccessValidationResponse(
            allowed=False,
            is_public=False,
            reason=(
                "Repository not found or you do not have access. "
                "If it is private, please log in with GitHub OAuth."
            ),
        )
    if resp.status_code == 401:
        return AccessValidationResponse(
            allowed=False,
            is_public=False,
            reason="GitHub token is invalid or expired. Please re-authenticate.",
        )
    if resp.status_code != 200:
        return AccessValidationResponse(
            allowed=False,
            is_public=False,
            reason=f"GitHub API error ({resp.status_code}): {resp.text[:200]}",
        )

    data = resp.json()
    is_private = bool(data.get("private", False))
    permissions = data.get("permissions", {}) or {}

    # Public repos are always allowed (FR-003)
    if not is_private:
        return AccessValidationResponse(
            allowed=True,
            is_public=True,
            repo_full_name=data.get("full_name", ""),
            permission="read",
            default_branch=data.get("default_branch", "main"),
            private=False,
            reason="Public repository — read access granted by default.",
        )

    # Private repo — must have at least pull permission (FR-002)
    if permissions.get("admin"):
        perm = "admin"
    elif permissions.get("push"):
        perm = "write"
    elif permissions.get("pull"):
        perm = "read"
    else:
        return AccessValidationResponse(
            allowed=False,
            is_public=False,
            repo_full_name=data.get("full_name", ""),
            private=True,
            reason="You do not have read access to this private repository.",
        )

    return AccessValidationResponse(
        allowed=True,
        is_public=False,
        repo_full_name=data.get("full_name", ""),
        permission=perm,
        default_branch=data.get("default_branch", "main"),
        private=True,
        reason=f"Authorized — {perm} access confirmed via GitHub.",
    )
