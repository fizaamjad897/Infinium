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
