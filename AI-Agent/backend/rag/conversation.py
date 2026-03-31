"""
Conversation History Store
- JSON file-based conversation persistence
- Tracks multi-turn Q&A for follow-up context
- Auto-cleanup of expired conversations
"""
import os
import json
import uuid
import time
from datetime import datetime, timedelta
from typing import Optional
from backend.config import settings

CONVERSATIONS_FILE = os.path.join(settings.DATA_DIR, "conversations.json")


def _load_conversations() -> dict:
    """Load all conversations from disk."""
    if os.path.exists(CONVERSATIONS_FILE):
        try:
            with open(CONVERSATIONS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_conversations(convos: dict):
    """Save all conversations to disk."""
    try:
        os.makedirs(os.path.dirname(CONVERSATIONS_FILE), exist_ok=True)
        with open(CONVERSATIONS_FILE, "w") as f:
            json.dump(convos, f, indent=2, default=str)
    except Exception:
        pass


def start_conversation(repo_name: str = "", user_login: str = "") -> str:
    """Create a new conversation and return its ID."""
    convos = _load_conversations()
    convo_id = str(uuid.uuid4())[:8]

    convos[convo_id] = {
        "id": convo_id,
        "repo_name": repo_name,
        "user_login": user_login,
        "turns": [],
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
    }

    _save_conversations(convos)
    return convo_id


def add_turn(
    conversation_id: str,
    query: str,
    answer: str,
    sources: list = None,
) -> dict:
    """Add a Q&A turn to a conversation."""
    convos = _load_conversations()

    if conversation_id not in convos:
        # Auto-create if not found
        convos[conversation_id] = {
            "id": conversation_id,
            "repo_name": "",
            "user_login": "",
            "turns": [],
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
        }

    turn = {
        "query": query,
        "answer_preview": answer[:300],
        "sources_count": len(sources) if sources else 0,
        "timestamp": datetime.now().isoformat(),
    }

    convos[conversation_id]["turns"].append(turn)
    convos[conversation_id]["updated_at"] = datetime.now().isoformat()

    # Keep only last N turns
    max_turns = settings.MAX_CONVERSATION_TURNS
    if len(convos[conversation_id]["turns"]) > max_turns:
        convos[conversation_id]["turns"] = convos[conversation_id]["turns"][-max_turns:]

    _save_conversations(convos)
    return turn


def get_history(conversation_id: str, max_turns: int = None) -> list[dict]:
    """Get conversation history as context for follow-up questions."""
    max_turns = max_turns or settings.MAX_CONVERSATION_TURNS
    convos = _load_conversations()

    if conversation_id not in convos:
        return []

    turns = convos[conversation_id]["turns"]
    return turns[-max_turns:]


def format_history_for_prompt(conversation_id: str) -> str:
    """Format conversation history into a prompt-ready string."""
    history = get_history(conversation_id, max_turns=5)

    if not history:
        return ""

    parts = ["Previous conversation context:"]
    for turn in history:
        parts.append(f"User: {turn['query']}")
        parts.append(f"Assistant: {turn['answer_preview']}")
        parts.append("")

    return "\n".join(parts)


def cleanup_expired():
    """Remove conversations older than CONVERSATION_TTL_HOURS."""
    convos = _load_conversations()
    ttl_hours = settings.CONVERSATION_TTL_HOURS
    cutoff = datetime.now() - timedelta(hours=ttl_hours)

    expired = [
        cid for cid, c in convos.items()
        if datetime.fromisoformat(c.get("updated_at", "2000-01-01")) < cutoff
    ]

    for cid in expired:
        del convos[cid]

    if expired:
        _save_conversations(convos)
        print(f"   🧹 Cleaned up {len(expired)} expired conversations")

    return len(expired)
