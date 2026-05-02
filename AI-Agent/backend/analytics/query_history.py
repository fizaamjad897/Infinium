"""
Query History Store
- Persists every query made through the system, scoped per user
- Powers the personal dashboard analytics (FR-011)
- Feeds the automated documentation summarizer (FR-012)
"""
from __future__ import annotations
import os
import json
from datetime import datetime, timedelta
from typing import Optional
from collections import Counter
from backend.config import settings

HISTORY_FILE = os.path.join(settings.DATA_DIR, "query_history.json")


def _load() -> list[dict]:
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def _save(history: list[dict]):
    try:
        os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
        with open(HISTORY_FILE, "w") as f:
            json.dump(history, f, indent=2, default=str)
    except Exception:
        pass


def record_query(
    query: str,
    answer: str,
    repo_name: str = "",
    user_login: str = "anonymous",
    sources_count: int = 0,
    duration_ms: int = 0,
    model: str = "",
    interface: str = "api",  # api | ide | slack | dashboard
) -> dict:
    """Persist a query event."""
    history = _load()
    entry = {
        "id": (history[-1]["id"] + 1) if history else 1,
        "query": query,
        "answer_preview": (answer or "")[:300],
        "repo_name": repo_name,
        "user_login": user_login,
        "sources_count": sources_count,
        "duration_ms": duration_ms,
        "model": model,
        "interface": interface,
        "created_at": datetime.now().isoformat(),
    }
    history.append(entry)
    # Cap to last 5000 entries to keep file size sane
    if len(history) > 5000:
        history = history[-5000:]
    _save(history)
    return entry


def list_for_user(user_login: str, limit: int = 50) -> list[dict]:
    """Recent queries by a single user, newest first."""
    history = _load()
    user_history = [h for h in history if h.get("user_login") == user_login]
    return list(reversed(user_history[-limit:]))


def list_all(limit: int = 100) -> list[dict]:
    history = _load()
    return list(reversed(history[-limit:]))


def stats_for_user(user_login: str) -> dict:
    """Per-user analytics for the personal dashboard."""
    history = _load()
    user_history = [h for h in history if h.get("user_login") == user_login]

    if not user_history:
        return {
            "total_queries": 0,
            "queries_last_7_days": 0,
            "avg_duration_ms": 0,
            "by_repo": {},
            "by_interface": {},
            "top_keywords": [],
        }

    week_ago = datetime.now() - timedelta(days=7)
    recent = [h for h in user_history
              if datetime.fromisoformat(h["created_at"]) > week_ago]

    durations = [h.get("duration_ms", 0) for h in user_history if h.get("duration_ms")]
    avg_dur = round(sum(durations) / len(durations), 0) if durations else 0

    by_repo = Counter(h.get("repo_name", "") for h in user_history)
    by_interface = Counter(h.get("interface", "api") for h in user_history)

    # Crude keyword extraction — strip stopwords
    stopwords = {"the", "a", "an", "is", "are", "of", "and", "or", "to", "in",
                 "for", "on", "with", "how", "what", "why", "where", "when",
                 "do", "does", "did", "this", "that", "it", "be", "by", "as"}
    words = []
    for h in user_history:
        for w in h["query"].lower().split():
            w = "".join(c for c in w if c.isalnum())
            if w and w not in stopwords and len(w) > 2:
                words.append(w)
    top_keywords = Counter(words).most_common(10)

    return {
        "total_queries": len(user_history),
        "queries_last_7_days": len(recent),
        "avg_duration_ms": avg_dur,
        "by_repo": dict(by_repo),
        "by_interface": dict(by_interface),
        "top_keywords": [{"keyword": k, "count": c} for k, c in top_keywords],
    }


def stats_global() -> dict:
    """Whole-system stats (admin view)."""
    history = _load()
    if not history:
        return {"total": 0}

    by_user = Counter(h.get("user_login", "anonymous") for h in history)
    by_repo = Counter(h.get("repo_name", "") for h in history)
    by_interface = Counter(h.get("interface", "api") for h in history)

    return {
        "total": len(history),
        "unique_users": len(by_user),
        "by_user_top10": by_user.most_common(10),
        "by_repo_top10": by_repo.most_common(10),
        "by_interface": dict(by_interface),
    }


def frequent_queries(min_count: int = 2, limit: int = 20) -> list[dict]:
    """Find queries (or near-duplicates) asked multiple times — feeds FR-012."""
    history = _load()
    # Group by normalized query string
    counts = Counter()
    examples: dict[str, dict] = {}
    for h in history:
        q = h["query"].lower().strip()
        # Normalize to first 80 chars to catch near-duplicates
        key = q[:80]
        counts[key] += 1
        if key not in examples:
            examples[key] = h

    frequent = [
        {
            "query": examples[k]["query"],
            "count": c,
            "repo_name": examples[k].get("repo_name", ""),
            "last_asked": examples[k].get("created_at", ""),
        }
        for k, c in counts.most_common(limit)
        if c >= min_count
    ]
    return frequent


def queries_per_day(days: int = 14) -> list[dict]:
    """Time-series of query volume per day, oldest first."""
    history = _load()
    cutoff = datetime.now() - timedelta(days=days)

    by_day: dict[str, int] = {}
    for h in history:
        try:
            dt = datetime.fromisoformat(h["created_at"])
            if dt < cutoff:
                continue
            day = dt.strftime("%Y-%m-%d")
            by_day[day] = by_day.get(day, 0) + 1
        except Exception:
            continue

    # Fill in missing days with 0
    series = []
    for i in range(days, -1, -1):
        day = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        series.append({"date": day, "count": by_day.get(day, 0)})
    return series
