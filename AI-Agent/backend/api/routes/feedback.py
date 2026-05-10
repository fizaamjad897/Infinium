"""
Generic feedback routes — works for any AI surface (query / refactor / explain).

  POST /api/feedback                  → submit feedback on any action
  GET  /api/feedback/stats            → aggregate stats (optionally per target_type)
  GET  /api/feedback                  → list recent feedback (optionally filtered)

The legacy ``/api/query/feedback`` endpoint still works and is unchanged —
this is an additive, generic surface so the FE can rate refactor diffs etc.
"""
from __future__ import annotations
from typing import Optional
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from backend.feedback.store import (
    save_feedback,
    get_feedback_stats,
    get_all_feedbacks,
)
from backend.auth.jwt_handler import get_current_user_optional


router = APIRouter(prefix="/api/feedback", tags=["Feedback"])


_VALID_TARGETS = {"query", "refactor", "explain", "rewrite", "search"}


class FeedbackRequest(BaseModel):
    target_type: str = Field("query", description=f"One of: {sorted(_VALID_TARGETS)}")
    target_id: str = Field("", description="Opaque link back to the rated action (e.g. conversation_id, file:symbol)")
    query: str = Field("", description="The user-visible prompt/instruction shown in the UI")
    answer: str = Field("", description="The generated output (full or preview — only first 200 chars stored)")
    rating: int = Field(..., ge=1, le=5, description="1=bad, 5=excellent. Use 1/5 if you only expose thumbs-down/up.")
    comment: str = Field("", description="Optional free-text feedback")
    repo_name: str = Field("", description="Repo the action ran against")
    metadata: dict = Field(default_factory=dict, description="Extra context (model, granularity, etc.)")


@router.post("")
async def submit_feedback(
    req: FeedbackRequest,
    current_user: Optional[dict] = Depends(get_current_user_optional),
):
    """Record feedback on any AI surface.

    The FE should send ``target_type="refactor"`` for refactor diff ratings,
    ``"query"`` for query answers, etc. All entries land in the same JSON
    store and are visible together via ``GET /api/feedback`` and the dashboard.
    """
    target_type = req.target_type if req.target_type in _VALID_TARGETS else "query"
    user_login = (current_user or {}).get("login", "")
    entry = save_feedback(
        query=req.query,
        answer=req.answer,
        rating=req.rating,
        comment=req.comment,
        repo_name=req.repo_name,
        user_login=user_login,
        target_type=target_type,
        target_id=req.target_id,
        metadata=req.metadata,
    )
    return {"status": "feedback_saved", "entry": entry}


@router.get("/stats")
async def stats(target_type: Optional[str] = Query(None, description="Filter to one target_type")):
    """Aggregate counts/averages across all feedback. Pass ``?target_type=refactor``
    to scope the headline numbers, but the ``by_target`` breakdown always
    covers every type."""
    return get_feedback_stats(target_type=target_type)


@router.get("")
async def list_feedback(
    limit: int = Query(50, ge=1, le=500),
    target_type: Optional[str] = Query(None),
    repo_name: Optional[str] = Query(None),
):
    """Recent feedback entries, newest first. Filter by target_type or repo_name."""
    return get_all_feedbacks(limit=limit, target_type=target_type, repo_name=repo_name)


@router.get("/metrics")
async def metrics(
    days: int = Query(30, ge=1, le=365, description="Rolling window in days"),
    target_type: Optional[str] = Query(None, description="Filter to one target_type"),
    repo_name: Optional[str] = Query(None, description="Filter to one repo"),
):
    """User-perceived accuracy / quality metrics derived from the feedback log.

    Returns helpfulness rate, average rating, per-day time series, and
    per-target_type / per-model / per-repo breakdowns. Designed for a
    dashboard chart — refresh on every visit, no caching.

    Honest framing: this is *user-perceived* accuracy (👍/👎), not
    ground-truth accuracy. With no labeled test set this is the best signal
    we have, and it's the standard metric for production LLM apps.
    """
    from datetime import datetime, timedelta
    from collections import defaultdict

    fb = get_all_feedbacks(limit=10_000)  # whole store; small JSON so fine
    # Filters
    if target_type:
        fb = [f for f in fb if f.get("target_type", "query") == target_type]
    if repo_name:
        fb = [f for f in fb if f.get("repo_name") == repo_name]

    cutoff = datetime.now() - timedelta(days=days)
    def _parse_dt(s: str):
        try:
            return datetime.fromisoformat(s)
        except Exception:
            return None

    in_window = [f for f in fb if (d := _parse_dt(f.get("created_at", ""))) and d >= cutoff]

    def _summary(entries: list[dict]) -> dict:
        if not entries:
            return {"total": 0, "up": 0, "down": 0, "helpfulness_rate": 0.0, "average_rating": 0.0}
        up = sum(1 for e in entries if e.get("rating", 0) >= 4)
        down = sum(1 for e in entries if e.get("rating", 0) <= 2)
        rated = up + down  # ignore neutral 3s in the rate
        return {
            "total": len(entries),
            "up": up,
            "down": down,
            "helpfulness_rate": round(up / rated, 4) if rated else 0.0,
            "average_rating": round(sum(e.get("rating", 0) for e in entries) / len(entries), 2),
        }

    # Per-day series — exactly `days` buckets so the chart has consistent x-axis
    by_day_map: dict[str, list[dict]] = defaultdict(list)
    for e in in_window:
        d = _parse_dt(e.get("created_at", ""))
        if d:
            by_day_map[d.date().isoformat()].append(e)
    by_day = []
    for i in range(days):
        date = (datetime.now().date() - timedelta(days=days - 1 - i)).isoformat()
        entries = by_day_map.get(date, [])
        s = _summary(entries)
        by_day.append({"date": date, **s})

    # Per-dimension breakdowns (use the in-window slice so they match the headline)
    def _group_by(key: str) -> dict:
        groups: dict[str, list[dict]] = defaultdict(list)
        for e in in_window:
            val = e.get(key) or (e.get("metadata") or {}).get(key) or "unknown"
            groups[str(val)].append(e)
        return {k: _summary(v) for k, v in groups.items()}

    # recent negatives — useful triage list (last 10 thumbs-down with comments)
    recent_negatives = [
        {
            "id": e.get("id"),
            "target_type": e.get("target_type"),
            "repo_name": e.get("repo_name"),
            "query": (e.get("query") or "")[:200],
            "comment": e.get("comment") or "",
            "rating": e.get("rating"),
            "created_at": e.get("created_at"),
        }
        for e in in_window if e.get("rating", 0) <= 2
    ][:10]

    return {
        "window_days": days,
        "filters": {"target_type": target_type, "repo_name": repo_name},
        "summary": _summary(in_window),
        "by_day": by_day,
        "by_target_type": _group_by("target_type"),
        "by_model": _group_by("model"),
        "by_repo": _group_by("repo_name"),
        "recent_negatives": recent_negatives,
    }
