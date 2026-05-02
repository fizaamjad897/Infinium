"""
JWT Token Handler
- Create and verify JWT tokens
- FastAPI dependency for protected routes
"""
import jwt
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import HTTPException, Request, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from backend.config import settings

security = HTTPBearer(auto_error=False)


def create_token(user_data: dict) -> str:
    """Create a JWT token with user data.
    
    Args:
        user_data: dict with github_id, login, name, avatar_url
    
    Returns:
        JWT token string
    """
    payload = {
        **user_data,
        "exp": datetime.now(timezone.utc) + timedelta(hours=settings.JWT_EXPIRE_HOURS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def verify_token(token: str) -> dict:
    """Verify and decode a JWT token.
    
    Returns:
        Decoded payload dict
    
    Raises:
        HTTPException if token is invalid or expired
    """
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Security(security),
) -> dict:
    """FastAPI dependency to get the current authenticated user.

    Usage:
        @app.get("/protected")
        async def protected(user: dict = Depends(get_current_user)):
            return user
    """
    if credentials is None:
        raise HTTPException(status_code=401, detail="Not authenticated. Please login via /api/auth/github")

    return verify_token(credentials.credentials)


async def get_current_user_optional(
    credentials: HTTPAuthorizationCredentials = Security(security),
) -> Optional[dict]:
    """Soft auth — returns the user dict if a valid JWT is present, else None.

    Use this for endpoints that work for both anonymous and logged-in users
    (e.g. ingesting public repos, submitting feedback).
    """
    if credentials is None:
        return None
    try:
        return verify_token(credentials.credentials)
    except HTTPException:
        return None
