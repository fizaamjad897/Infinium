"""
Automated Documentation Summarization (FR-012)

Mines the query history for recurring questions, then asks the LLM to draft
documentation that answers them. Also reports gaps (questions where retrieval
returned poor or no context).

  GET  /api/docs/gaps                 → questions with no/poor answers (gap analysis)
  GET  /api/docs/frequent             → most common questions (with counts)
  POST /api/docs/summarize            → LLM-drafted FAQ for a repo from frequent queries
  POST /api/docs/summarize/{repo}     → convenience wrapper for a single repo
"""
from __future__ import annotations
from fastapi import APIRouter
from pydantic import BaseModel, Field
from typing import Optional

from backend.analytics.query_history import frequent_queries, list_all
from backend.rag.query_engine import ask
from backend.rag.generator import _call_ollama_chat, SYNTHESIS_SYSTEM_PROMPT
from backend.config import settings

router = APIRouter(prefix="/api/docs", tags=["Documentation"])


class SummarizeRequest(BaseModel):
    repo_name: str = Field(..., description="Repo to draft documentation for")
    min_query_count: int = Field(2, ge=1, description="Minimum times a question must've been asked to be included")
    max_questions: int = Field(8, ge=1, le=20, description="Cap on questions in the FAQ")


@router.get("/gaps")
async def docs_gaps():
    """Identify queries the system answered poorly (no chunks / very short answers).

    These are documentation gaps — the codebase doesn't yet contain the answer,
    so a human should write it.
    """
    history = list_all(limit=500)
    gaps: list[dict] = []
    for h in history:
        # Heuristic: zero sources OR an apologetic short answer
        if h.get("sources_count", 0) == 0:
            gaps.append({
                "query": h["query"],
                "repo_name": h.get("repo_name", ""),
                "asked_at": h.get("created_at", ""),
                "reason": "no relevant context retrieved",
            })
        elif "couldn't find" in (h.get("answer_preview", "") or "").lower():
            gaps.append({
                "query": h["query"],
                "repo_name": h.get("repo_name", ""),
                "asked_at": h.get("created_at", ""),
                "reason": "model could not answer with retrieved context",
            })
    return {"gaps": gaps[:50], "total": len(gaps)}


@router.get("/frequent")
async def docs_frequent(min_count: int = 2, limit: int = 20):
    """Most-asked questions across the system — feeds the FAQ generator."""
    return {"frequent": frequent_queries(min_count=min_count, limit=limit)}


@router.post("/summarize")
async def docs_summarize(req: SummarizeRequest):
    """Draft a documentation FAQ for a repo using its frequent questions.

    Process:
      1. Pick the top N most-asked questions (filtered by repo or '*all*')
      2. For each, run RAG to get the canonical answer
      3. Ask the LLM to merge them into a coherent doc draft

    Output is a Markdown string the user can paste into their docs.
    """
    # Step 1: pick frequent questions touching this repo
    all_frequent = frequent_queries(min_count=req.min_query_count, limit=50)
    questions = [
        f for f in all_frequent
        if f.get("repo_name") in (req.repo_name, "", "*all*")
    ][: req.max_questions]

    if not questions:
        return {
            "repo_name": req.repo_name,
            "draft": "",
            "questions_used": [],
            "message": (
                "Not enough query history yet to summarise. "
                "Ask a few questions about this repo first, then re-run."
            ),
        }

    # Step 2: get canonical answers via RAG
    qa_pairs = []
    for q in questions:
        try:
            result = ask(
                query=q["query"],
                repo_name=req.repo_name,
                user_login="docs-summarizer",
                interface="api",
            )
            qa_pairs.append({
                "question": q["query"],
                "answer": result.get("answer", ""),
                "ask_count": q["count"],
            })
        except Exception as e:
            print(f"   ⚠️ Skipping question due to error: {e}")

    if not qa_pairs:
        return {
            "repo_name": req.repo_name,
            "draft": "",
            "questions_used": [],
            "message": "Could not generate answers — check ingestion + Ollama.",
        }

    # Step 3: merge into a coherent doc draft via LLM
    qa_block = "\n\n".join(
        f"Q ({p['ask_count']}× asked): {p['question']}\nA: {p['answer']}"
        for p in qa_pairs
    )
    prompt = f"""Below is a list of frequently-asked developer questions and their answers,
collected from a knowledge agent's query log on the repository "{req.repo_name}".

Your job: turn this into a clean, concise developer-facing FAQ in Markdown.
Group related questions, dedupe overlap, fix tone, but DO NOT invent facts —
keep the technical content faithful to the answers below.

Frequently asked questions:
{qa_block}

Output only the Markdown FAQ — start with `# {req.repo_name} — Frequently Asked Questions`.
"""

    chat = _call_ollama_chat(
        model=settings.LLM_MODEL,
        messages=[
            {"role": "system", "content": SYNTHESIS_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        context_window=settings.LLM_CONTEXT_WINDOW,
    )

    return {
        "repo_name": req.repo_name,
        "draft": chat.get("content", ""),
        "questions_used": qa_pairs,
        "model": settings.LLM_MODEL,
    }


@router.post("/summarize/{repo_name}")
async def docs_summarize_for_repo(repo_name: str):
    """Shortcut: summarise with default thresholds for a single repo."""
    return await docs_summarize(SummarizeRequest(repo_name=repo_name))
