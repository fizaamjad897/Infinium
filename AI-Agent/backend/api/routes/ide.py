"""
IDE Integration Routes (FR-009)

Designed for direct consumption by the VSCode extension (or any IDE plugin).
Accepts a code selection + an optional natural-language question, returns a
short, focused answer formatted for an IDE side-panel.

POST /api/ide/query        → Ask about selected code (with file path context)
POST /api/ide/explain      → "What does this code do?" — implicit question
POST /api/ide/improve      → "Suggest improvements / find issues" in selection
GET  /api/ide/repos        → Lightweight repo list for the IDE picker
"""
from __future__ import annotations
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from typing import Optional

from backend.rag.query_engine import ask, ask_all
from backend.ingestion.embedder import list_collections, get_collection_stats
from backend.auth.jwt_handler import get_current_user_optional

router = APIRouter(prefix="/api/ide", tags=["IDE Integration"])


# ── Request models ──

class IDEQueryRequest(BaseModel):
    query: str = Field(..., description="Natural language question from the developer")
    repo_name: Optional[str] = Field(None, description="Repo to scope to. If omitted, searches all indexed repos.")
    file_path: Optional[str] = Field(None, description="Path of the active file in the IDE")
    selected_code: Optional[str] = Field(None, description="Code currently selected in the editor")
    language: Optional[str] = Field(None, description="Language id of the active file (e.g. 'python', 'typescript')")
    use_dual_model: Optional[bool] = Field(False, description="Enable deep reasoning (slower)")


class IDEActionRequest(BaseModel):
    """Used by /explain and /improve — query is implicit."""
    repo_name: Optional[str] = Field(None)
    file_path: Optional[str] = Field(None)
    selected_code: str = Field(..., description="The code selection to operate on")
    language: Optional[str] = Field(None)


def _build_prompt(query: str, file_path: str | None, selected: str | None, lang: str | None) -> str:
    """Wrap the user's question with the editor context."""
    parts = [query.strip()]
    if file_path:
        parts.append(f"\nActive file: {file_path}")
    if selected:
        fence = lang or ""
        parts.append(f"\nSelected code:\n```{fence}\n{selected}\n```")
    return "\n".join(parts)


def _user_login(user: dict | None) -> str:
    return (user or {}).get("login", "anonymous")


def _format_for_ide(result: dict) -> dict:
    """Trim payload for the IDE — drop the heavy reasoning trace."""
    sources = result.get("sources", []) or []
    return {
        "answer": result.get("answer", ""),
        "sources": [
            {
                "source": s.get("source", ""),
                "repo": s.get("repo", ""),
                "type": s.get("type", "code"),
                "relevance": s.get("relevance", 0.0),
            }
            for s in sources[:10]
        ],
        "model": result.get("model", ""),
        "chunks_retrieved": result.get("chunks_retrieved", 0),
        "duration_ms": result.get("total_duration_ms", 0),
    }


@router.post("/query")
async def ide_query(
    req: IDEQueryRequest,
    current_user: Optional[dict] = Depends(get_current_user_optional),
):
    """Main entry point for the VSCode extension.

    If ``repo_name`` is provided, scopes to that repo; otherwise searches every
    indexed repository. The IDE is expected to surface ``answer`` directly in a
    side panel and link ``sources`` to file paths.
    """
    enriched = _build_prompt(req.query, req.file_path, req.selected_code, req.language)
    login = _user_login(current_user)

    if req.repo_name:
        result = ask(
            query=enriched,
            repo_name=req.repo_name,
            use_hybrid=True,
            use_dual_model=req.use_dual_model,
            user_login=login,
            interface="ide",
        )
    else:
        result = ask_all(
            query=enriched,
            use_hybrid=True,
            use_dual_model=req.use_dual_model,
            user_login=login,
            interface="ide",
        )
    return _format_for_ide(result)


@router.post("/explain")
async def ide_explain(
    req: IDEActionRequest,
    current_user: Optional[dict] = Depends(get_current_user_optional),
):
    """Explain the selected code in plain language."""
    query = (
        "Explain what the selected code below does, line by line if helpful. "
        "Identify functions/classes used and reference where else they're defined."
    )
    enriched = _build_prompt(query, req.file_path, req.selected_code, req.language)
    login = _user_login(current_user)

    if req.repo_name:
        result = ask(query=enriched, repo_name=req.repo_name,
                     user_login=login, interface="ide")
    else:
        result = ask_all(query=enriched, user_login=login, interface="ide")
    return _format_for_ide(result)


@router.post("/improve")
async def ide_improve(
    req: IDEActionRequest,
    current_user: Optional[dict] = Depends(get_current_user_optional),
):
    """Suggest improvements / find potential issues in the selected code."""
    query = (
        "Review the selected code for bugs, performance issues, and idiomatic "
        "improvements. Cite related code from the repository when suggesting changes."
    )
    enriched = _build_prompt(query, req.file_path, req.selected_code, req.language)
    login = _user_login(current_user)

    if req.repo_name:
        result = ask(query=enriched, repo_name=req.repo_name,
                     user_login=login, interface="ide")
    else:
        result = ask_all(query=enriched, user_login=login, interface="ide")
    return _format_for_ide(result)


@router.get("/repos")
async def ide_repos():
    """Lightweight list of indexed repos for the IDE's repo picker."""
    repos = []
    for name in list_collections():
        stats = get_collection_stats(name)
        repos.append({
            "repo_name": name,
            "chunks": stats.get("chunks", 0),
            "status": stats.get("status", "unknown"),
        })
    return {"repos": repos}
