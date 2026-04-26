"""
Query API Routes
- Single repo query (/api/query)
- Multi-repo / whole codebase query (/api/query/all)
- Feedback submission (/api/query/feedback)
- Conversation management (/api/conversation)
"""
from fastapi import APIRouter, Depends
from typing import Optional

from backend.api.models import (
    QueryRequest, QueryAllRequest, QueryResponse,
    FeedbackRequest,
    ConversationStartRequest, ConversationStartResponse,
)
from backend.rag.query_engine import ask, ask_all
from backend.rag.conversation import start_conversation, get_history, cleanup_expired
from backend.feedback.store import save_feedback, get_feedback_stats
from backend.auth.jwt_handler import get_current_user

router = APIRouter(prefix="/api", tags=["Query"])


@router.post("/query", response_model=QueryResponse)
async def query_repo(req: QueryRequest):
    """Ask a question about a specific repository.

    This searches within a single indexed repo and generates an answer
    with cited sources. Supports dual-model reasoning pipeline.
    """
    result = ask(
        query=req.query,
        repo_name=req.repo_name,
        use_hybrid=req.use_hybrid,
        use_expansion=req.use_expansion,
        use_dual_model=req.use_dual_model,
        top_k=req.top_k,
        model=req.model,
        conversation_id=req.conversation_id,
        branch_filter=req.branch_filter,
        chunk_types=req.chunk_types,
    )
    return result


@router.post("/query/all", response_model=QueryResponse)
async def query_all_repos(req: QueryAllRequest):
    """Ask a question across ALL indexed repositories (whole codebase mode).

    This is the premium feature — like Copilot but searching across your
    entire organization's codebase. Returns cross-referenced answers
    with sources from multiple repositories.
    """
    result = ask_all(
        query=req.query,
        repo_names=req.repo_names,
        use_hybrid=req.use_hybrid,
        use_expansion=req.use_expansion,
        use_dual_model=req.use_dual_model,
        top_k=req.top_k,
        model=req.model,
        conversation_id=req.conversation_id,
        branch_filter=req.branch_filter,
        chunk_types=req.chunk_types,
    )
    return result


@router.post("/conversation/start", response_model=ConversationStartResponse)
async def start_new_conversation(req: ConversationStartRequest):
    """Start a new conversation for multi-turn follow-up questions.

    Returns a conversation_id that you can pass to /api/query or /api/query/all
    to maintain context across multiple questions.
    """
    convo_id = start_conversation(repo_name=req.repo_name)
    return ConversationStartResponse(
        conversation_id=convo_id,
        message=f"Conversation started. Pass conversation_id='{convo_id}' in your query requests for follow-ups.",
    )


@router.get("/conversation/{conversation_id}/history")
async def get_conversation_history(conversation_id: str):
    """Get the history of a conversation."""
    history = get_history(conversation_id)
    return {"conversation_id": conversation_id, "turns": history}


@router.post("/query/feedback")
async def submit_feedback(
    req: FeedbackRequest,
    current_user: Optional[dict] = Depends(get_current_user),
):
    """Submit feedback on an answer quality.

    This helps improve the system over time through the feedback loop.
    """
    user_login = current_user.get("login", "anonymous") if current_user else "anonymous"

    entry = save_feedback(
        query=req.query,
        answer=req.answer,
        rating=req.rating,
        comment=req.comment,
        repo_name=req.repo_name,
        user_login=user_login,
    )

    return {"status": "feedback_saved", "entry": entry}


@router.get("/query/feedback/stats")
async def feedback_stats():
    """Get feedback summary statistics."""
    return get_feedback_stats()
