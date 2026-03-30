"""
GitHub OAuth Authentication
- OAuth authorization code flow
- Token exchange and user profile fetch
- FastAPI routes for login/callback/logout
"""
import httpx
from fastapi import APIRouter, HTTPException, Response, Request, Depends
from fastapi.responses import RedirectResponse
from backend.config import settings
from backend.auth.jwt_handler import create_token, get_current_user
from backend.auth.models import UserStore

router = APIRouter(prefix="/api/auth", tags=["Authentication"])
user_store = UserStore()


@router.get("/github")
async def github_login():
    """Redirect user to GitHub OAuth authorization page."""
    if not settings.GITHUB_CLIENT_ID:
        raise HTTPException(
            status_code=500,
            detail="GitHub OAuth not configured. Set GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET in .env"
        )
    
    github_auth_url = (
        f"https://github.com/login/oauth/authorize"
        f"?client_id={settings.GITHUB_CLIENT_ID}"
        f"&redirect_uri={settings.GITHUB_REDIRECT_URI}"
        f"&scope=repo,read:user,user:email"
    )
    return RedirectResponse(url=github_auth_url)


@router.get("/github/callback")
async def github_callback(code: str):
    """Handle GitHub OAuth callback — exchange code for token, fetch profile, issue JWT."""
    if not code:
        raise HTTPException(status_code=400, detail="Authorization code missing")
    
    # 1. Exchange code for access token
    async with httpx.AsyncClient() as client:
        token_response = await client.post(
            "https://github.com/login/oauth/access_token",
            json={
                "client_id": settings.GITHUB_CLIENT_ID,
                "client_secret": settings.GITHUB_CLIENT_SECRET,
                "code": code,
                "redirect_uri": settings.GITHUB_REDIRECT_URI,
            },
            headers={"Accept": "application/json"},
            timeout=15,
        )
    
    token_data = token_response.json()
    access_token = token_data.get("access_token")
    
    if not access_token:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to get access token: {token_data.get('error_description', 'Unknown error')}"
        )
    
    # 2. Fetch user profile from GitHub
    async with httpx.AsyncClient() as client:
        user_response = await client.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=10,
        )
    
    if user_response.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to fetch GitHub profile")
    
    github_user = user_response.json()
    
    # 3. Store/update user
    user = user_store.upsert_user(
        github_id=github_user["id"],
        login=github_user["login"],
        name=github_user.get("name", ""),
        email=github_user.get("email", ""),
        avatar_url=github_user.get("avatar_url", ""),
        access_token=access_token,
    )
    
    # 4. Issue JWT
    jwt_token = create_token({
        "github_id": user["github_id"],
        "login": user["login"],
        "name": user["name"],
        "avatar_url": user["avatar_url"],
    })
    
    # Return token (frontend will store it)
    # In production, redirect to frontend with token in URL or set as HttpOnly cookie
    return {
        "token": jwt_token,
        "user": {
            "login": user["login"],
            "name": user["name"],
            "avatar_url": user["avatar_url"],
            "github_id": user["github_id"],
        },
    }


@router.get("/me")
async def get_me(current_user: dict = Depends(get_current_user)):
    """Get current authenticated user's info."""
    user = user_store.get_user(current_user["github_id"])
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    return {
        "login": user["login"],
        "name": user["name"],
        "avatar_url": user["avatar_url"],
        "github_id": user["github_id"],
    }


@router.post("/logout")
async def logout(current_user: dict = Depends(get_current_user)):
    """Logout — in a stateless JWT setup, the frontend simply discards the token."""
    return {"message": "Logged out successfully"}


@router.get("/github/repos")
async def list_github_repos(current_user: dict = Depends(get_current_user)):
    """List the authenticated user's GitHub repositories.
    
    This uses the stored GitHub access token to fetch repos the user has access to.
    """
    user = user_store.get_user(current_user["github_id"])
    if not user or not user.get("access_token"):
        raise HTTPException(status_code=401, detail="GitHub token not found. Please re-authenticate.")
    
    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://api.github.com/user/repos",
            headers={
                "Authorization": f"Bearer {user['access_token']}",
                "Accept": "application/vnd.github+json",
            },
            params={"per_page": 100, "sort": "updated"},
            timeout=15,
        )
    
    if response.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to fetch repositories")
    
    repos = response.json()
    return [
        {
            "full_name": r["full_name"],
            "name": r["name"],
            "description": r.get("description", ""),
            "private": r["private"],
            "language": r.get("language", ""),
            "clone_url": r["clone_url"],
            "html_url": r["html_url"],
            "stars": r.get("stargazers_count", 0),
            "updated_at": r.get("updated_at", ""),
        }
        for r in repos
    ]
