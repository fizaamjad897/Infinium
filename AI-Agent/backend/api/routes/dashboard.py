"""
Personal Dashboard Routes (FR-011)

Per-user, personalized analytics:
  GET  /api/dashboard               → all-in-one dashboard payload
  GET  /api/dashboard/repos         → user's repos with status + chunk counts
  GET  /api/dashboard/stats         → query analytics for the logged-in user
  GET  /api/dashboard/history       → recent queries by the logged-in user
  GET  /api/dashboard/timeseries    → queries-per-day for the logged-in user

A user "owns" a repo here if they were the one who ingested it (tracked in the
ingestion status record's `ingested_by` field, populated by the ingest routes).
For backwards compatibility, repos with no `ingested_by` are visible to everyone.
"""
from __future__ import annotations
from fastapi import APIRouter, Depends
from typing import Optional

from backend.auth.jwt_handler import get_current_user
from backend.ingestion.pipeline import get_all_repos, get_ingestion_status
from backend.ingestion.embedder import get_collection_stats
from backend.analytics.query_history import (
    list_for_user, stats_for_user, queries_per_day,
)
from backend.feedback.store import get_feedback_stats

router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"])


def _user_repos(user_login: str) -> list[dict]:
    """Repos owned by this user — falls back to all repos for legacy rows."""
    all_repos = get_all_repos()
    owned = []
    for r in all_repos:
        owner = r.get("ingested_by", "")
        if not owner or owner == user_login:
            stats = get_collection_stats(r["repo_name"])
            r["chunks"] = stats.get("chunks", 0)
            owned.append(r)
    return owned


@router.get("")
async def dashboard(current_user: dict = Depends(get_current_user)):
    """All-in-one dashboard payload for the personal home screen."""
    login = current_user.get("login", "anonymous")
    return {
        "user": {
            "login": login,
            "name": current_user.get("name", ""),
            "avatar_url": current_user.get("avatar_url", ""),
        },
        "repos": _user_repos(login),
        "stats": stats_for_user(login),
        "recent_queries": list_for_user(login, limit=10),
        "timeseries": queries_per_day(days=14),
        "feedback": get_feedback_stats(),
    }


@router.get("/repos")
async def dashboard_repos(current_user: dict = Depends(get_current_user)):
    """List of repos owned by the user — drives the repository-card grid."""
    login = current_user.get("login", "anonymous")
    return {"repos": _user_repos(login)}


@router.get("/stats")
async def dashboard_stats(current_user: dict = Depends(get_current_user)):
    """Aggregated query analytics for the logged-in user."""
    login = current_user.get("login", "anonymous")
    return stats_for_user(login)


@router.get("/history")
async def dashboard_history(
    limit: int = 50,
    current_user: dict = Depends(get_current_user),
):
    """Recent query history for the logged-in user."""
    login = current_user.get("login", "anonymous")
    return {"queries": list_for_user(login, limit=limit)}


@router.get("/timeseries")
async def dashboard_timeseries(
    days: int = 14,
    current_user: dict = Depends(get_current_user),
):
    """Queries-per-day series — drives the activity sparkline."""
    return {"series": queries_per_day(days=days)}
