"""
Feedback Storage
- JSON file-based feedback for prototype
- Tracks answer quality ratings and comments
"""
import os
import json
from datetime import datetime
from typing import Optional
from backend.config import settings

FEEDBACK_FILE = os.path.join(settings.DATA_DIR, "feedback.json")


def save_feedback(
    query: str,
    answer: str,
    rating: int,
    comment: str = "",
    repo_name: str = "",
    user_login: str = "",
    target_type: str = "query",
    target_id: str = "",
    metadata: Optional[dict] = None,
) -> dict:
    """Save user feedback on any AI surface (query, refactor, explain, …).

    Args:
        query: The user-visible prompt/instruction (the "question" shown in UI).
        answer: The generated output (or a preview — only first 200 chars stored).
        rating: 1 (bad) to 5 (excellent). Use 1/5 for thumbs-down/up if you only
                expose binary feedback in the UI.
        comment: Optional free-text comment.
        repo_name: Repo the action was performed against.
        user_login: GitHub username.
        target_type: What kind of thing was rated. One of:
                     "query" | "refactor" | "explain" | "rewrite" | "search".
        target_id: Optional opaque ID linking back to the original action
                   (e.g. conversation_id for queries, file_path:symbol for refactors).
        metadata: Arbitrary extra fields (model used, granularity, etc.)
                  serialised alongside the entry.
    """
    feedback_entry = {
        "id": _next_id(),
        "target_type": target_type,
        "target_id": target_id,
        "query": query,
        "answer_preview": (answer or "")[:200],
        "rating": max(1, min(5, rating)),
        "comment": comment,
        "repo_name": repo_name,
        "user_login": user_login,
        "metadata": metadata or {},
        "created_at": datetime.now().isoformat(),
    }

    feedbacks = _load_feedbacks()
    feedbacks.append(feedback_entry)
    _save_feedbacks(feedbacks)

    # In-context reinforcement — embed the prompt into a Chroma memory so
    # similar future prompts can be steered toward the patterns that scored
    # well (and away from the ones that scored badly). Best-effort: if the
    # embed model is down we still return the entry from the JSON store.
    try:
        from backend.feedback.memory import remember
        remember(feedback_entry)
    except Exception as e:
        print(f"   ⚠️ feedback memory hook failed (non-fatal): {e}")

    return feedback_entry


def get_feedback_stats(target_type: Optional[str] = None) -> dict:
    """Get summary statistics across all feedback, or filtered by target_type."""
    feedbacks = _load_feedbacks()
    if target_type:
        feedbacks = [f for f in feedbacks if f.get("target_type", "query") == target_type]

    if not feedbacks:
        return {"total": 0, "average_rating": 0, "breakdown": {}, "by_target": {}}

    ratings = [f["rating"] for f in feedbacks]
    breakdown = {i: ratings.count(i) for i in range(1, 6)}

    # Per-target-type slice across all feedback (always shown, ignores filter)
    all_fb = _load_feedbacks()
    by_target: dict[str, dict] = {}
    for f in all_fb:
        t = f.get("target_type", "query")
        slot = by_target.setdefault(t, {"count": 0, "rating_sum": 0})
        slot["count"] += 1
        slot["rating_sum"] += f.get("rating", 0)
    for t, slot in by_target.items():
        slot["average_rating"] = round(slot["rating_sum"] / slot["count"], 2) if slot["count"] else 0
        slot.pop("rating_sum", None)

    return {
        "total": len(feedbacks),
        "average_rating": round(sum(ratings) / len(ratings), 2),
        "breakdown": breakdown,
        "by_target": by_target,
        "recent": feedbacks[-5:],
    }


def get_all_feedbacks(
    limit: int = 50,
    target_type: Optional[str] = None,
    repo_name: Optional[str] = None,
) -> list[dict]:
    """Get feedback entries, most recent first. Optionally filtered."""
    feedbacks = _load_feedbacks()
    if target_type:
        feedbacks = [f for f in feedbacks if f.get("target_type", "query") == target_type]
    if repo_name:
        feedbacks = [f for f in feedbacks if f.get("repo_name") == repo_name]
    return list(reversed(feedbacks[-limit:]))


def _load_feedbacks() -> list[dict]:
    if os.path.exists(FEEDBACK_FILE):
        try:
            with open(FEEDBACK_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def _save_feedbacks(feedbacks: list[dict]):
    try:
        with open(FEEDBACK_FILE, "w") as f:
            json.dump(feedbacks, f, indent=2, default=str)
    except Exception:
        pass


def _next_id() -> int:
    feedbacks = _load_feedbacks()
    if feedbacks:
        return max(f.get("id", 0) for f in feedbacks) + 1
    return 1
