"""
Repository Access Validation Routes (FR-002 / FR-003)

Two strictly-separated endpoints — pick the one that matches your repo type.
Each one rejects the other category with a clear redirect hint, so callers
never accidentally hit the wrong path.

  POST /api/access/public   → public-repo validation, no auth (FR-003)
  POST /api/access/private  → private-repo validation, auth required (FR-002)
"""
from __future__ import annotations
import re
import httpx
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel, Field
from typing import Optional

from backend.auth.jwt_handler import get_current_user, get_current_user_optional
from backend.auth.models import UserStore
from backend.config import settings

router = APIRouter(prefix="/api/access", tags=["Access Control"])
user_store = UserStore()


# ── Models ──

class PublicAccessRequest(BaseModel):
    repo_url: str = Field(..., description="Public GitHub repository URL")


class PrivateAccessRequest(BaseModel):
    repo_url: str = Field(..., description="Private GitHub repository URL")
    github_token: Optional[str] = Field(
        None,
        description=(
            "Optional override token. If omitted, the resolution order is: "
            "X-GitHub-Token header → JWT'd user's stored OAuth token → 401."
        ),
    )


class AccessResponse(BaseModel):
    allowed: bool
    is_public: bool
    repo_full_name: str = ""
    permission: str = ""           # admin / write / read / none
    default_branch: str = ""
    private: bool = False
    reason: str = ""


# ── Helpers ──

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


async def _fetch_repo_info(owner: str, repo: str, token: Optional[str]) -> tuple[int, dict]:
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    api_url = f"https://api.github.com/repos/{owner}/{repo}"
    async with httpx.AsyncClient() as client:
        resp = await client.get(api_url, headers=headers, timeout=10)
    try:
        body = resp.json()
    except Exception:
        body = {}
    return resp.status_code, body


# ── Public-only endpoint (FR-003) ──

@router.post("/public", response_model=AccessResponse)
async def validate_public(req: PublicAccessRequest):
    """Validate access to a **public** GitHub repository.

    No auth required. Uses the server-level ``GITHUB_API_TOKEN`` (if set in
    `.env`) only to lift the GitHub API rate limit from 60/hr to 5000/hr —
    the caller never supplies a token here.

    Behaviour:
      - Public repo  → ``allowed:true, is_public:true``.
      - Private repo → 400 "This is a private repository — use /api/access/private instead."
      - Missing repo → 404.
    """
    owner, repo = _parse(req.repo_url)

    # Server-level token only — never accepts user tokens on this endpoint
    token = settings.GITHUB_API_TOKEN or None
    status, data = await _fetch_repo_info(owner, repo, token)

    if status == 404:
        raise HTTPException(
            status_code=404,
            detail="Repository not found. If it exists and is private, use /api/access/private instead.",
        )
    if status != 200:
        raise HTTPException(
            status_code=502,
            detail=f"GitHub API error {status}: {(str(data)[:200])}",
        )

    if data.get("private", False):
        raise HTTPException(
            status_code=400,
            detail=(
                f"'{data.get('full_name','')}' is a PRIVATE repository. "
                "This endpoint only handles public repos. Use /api/access/private instead."
            ),
        )

    return AccessResponse(
        allowed=True,
        is_public=True,
        repo_full_name=data.get("full_name", ""),
        permission="read",
        default_branch=data.get("default_branch", "main"),
        private=False,
        reason="Public repository — read access granted by default.",
    )


# ── Private-only endpoint (FR-002) ──

@router.post("/private", response_model=AccessResponse)
async def validate_private(
    req: PrivateAccessRequest,
    x_github_token: Optional[str] = Header(None, description="GitHub token forwarded by the Node.js backend"),
    current_user: Optional[dict] = Depends(get_current_user_optional),
):
    """Validate access to a **private** GitHub repository.

    Auth required. Token resolution order:
      1. ``github_token`` field in the request body (explicit override)
      2. ``X-GitHub-Token`` header (Node.js backend path)
      3. Logged-in user's stored OAuth token (browser/IDE path)
      4. → 401

    Behaviour:
      - Has access → ``allowed:true, private:true, permission:read|write|admin``.
      - No access  → ``allowed:false`` with a clear reason (404-style).
      - Public repo → 400 "This is a public repository — use /api/access/public instead."
    """
    owner, repo = _parse(req.repo_url)

    # Resolve token in priority order — DO NOT fall back to server token
    # (server token belongs to the org, not the user; using it here would
    # let any caller see what the server token's owner can see)
    token = req.github_token or x_github_token
    if not token and current_user:
        user = user_store.get_user(current_user["github_id"])
        token = user.get("access_token") if user else None

    if not token:
        raise HTTPException(
            status_code=401,
            detail=(
                "Auth required for private-repo validation. Supply one of: "
                "(a) 'github_token' in the request body, "
                "(b) 'X-GitHub-Token' header, "
                "(c) JWT bearer token from /api/auth/github."
            ),
        )

    status, data = await _fetch_repo_info(owner, repo, token)

    if status == 404:
        # Could be no-access OR genuinely missing — same response either way
        return AccessResponse(
            allowed=False,
            is_public=False,
            private=True,
            reason=(
                "Repository not found, or your token does not have access. "
                "If your repo is private, ensure the PAT has 'repo' scope."
            ),
        )
    if status == 401:
        raise HTTPException(status_code=401, detail="GitHub token is invalid or expired.")
    if status != 200:
        raise HTTPException(status_code=502, detail=f"GitHub API error {status}: {str(data)[:200]}")

    # Public repo on the private endpoint → reject with redirect hint
    if not data.get("private", False):
        raise HTTPException(
            status_code=400,
            detail=(
                f"'{data.get('full_name','')}' is a PUBLIC repository. "
                "Use /api/access/public for public repos (no auth needed)."
            ),
        )

    permissions = data.get("permissions", {}) or {}
    if permissions.get("admin"):
        perm = "admin"
    elif permissions.get("push"):
        perm = "write"
    elif permissions.get("pull"):
        perm = "read"
    else:
        return AccessResponse(
            allowed=False,
            is_public=False,
            repo_full_name=data.get("full_name", ""),
            private=True,
            reason="You do not have read access to this private repository.",
        )

    return AccessResponse(
        allowed=True,
        is_public=False,
        repo_full_name=data.get("full_name", ""),
        permission=perm,
        default_branch=data.get("default_branch", "main"),
        private=True,
        reason=f"Authorized — {perm} access confirmed via GitHub.",
    )
