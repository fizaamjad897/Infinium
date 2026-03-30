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
) -> dict:
    """Save user feedback on an answer.
    
    Args:
        query: The original query
        answer: The generated answer
        rating: 1 (bad) to 5 (excellent)
        comment: Optional textual feedback
        repo_name: Which repo was queried
        user_login: GitHub username
    
    Returns:
        The saved feedback entry
    """
    feedback_entry = {
        "id": _next_id(),
        "query": query,
        "answer_preview": answer[:200],
        "rating": max(1, min(5, rating)),
        "comment": comment,
        "repo_name": repo_name,
        "user_login": user_login,
        "created_at": datetime.now().isoformat(),
    }
    
    feedbacks = _load_feedbacks()
    feedbacks.append(feedback_entry)
    _save_feedbacks(feedbacks)
    
    return feedback_entry


def get_feedback_stats() -> dict:
    """Get summary statistics of all feedback."""
    feedbacks = _load_feedbacks()
    
    if not feedbacks:
        return {"total": 0, "average_rating": 0, "breakdown": {}}
    
    ratings = [f["rating"] for f in feedbacks]
    breakdown = {i: ratings.count(i) for i in range(1, 6)}
    
    return {
        "total": len(feedbacks),
        "average_rating": round(sum(ratings) / len(ratings), 2),
        "breakdown": breakdown,
        "recent": feedbacks[-5:],
    }


def get_all_feedbacks(limit: int = 50) -> list[dict]:
    """Get all feedback entries, most recent first."""
    feedbacks = _load_feedbacks()
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
