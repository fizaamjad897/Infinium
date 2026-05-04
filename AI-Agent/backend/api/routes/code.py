"""
Code-Aware Routes — symbol lookup, snippet retrieval, and AI-driven rewriting.

  POST /api/code/find             → locate function/class definition(s) by name
  POST /api/code/callers          → find places that call a symbol
  POST /api/code/rewrite          → rewrite a snippet per an instruction
  POST /api/code/explain          → plain-language explanation of a snippet
  POST /api/code/cross-repo       → explicit comparison query across selected repos
"""
from __future__ import annotations
from typing import Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from backend.code_search.symbol_finder import find_symbol, find_callers
from backend.code_search.rewriter import rewrite_snippet, explain_snippet
from backend.rag.query_engine import ask_all
from backend.auth.jwt_handler import get_current_user_optional

router = APIRouter(prefix="/api/code", tags=["Code Search & Rewrite"])


# ── Symbol finder ──

class FindSymbolRequest(BaseModel):
    symbol: str = Field(..., description="Function / class / method / type name to find")
    repo_name: Optional[str] = Field(None, description="Scope to a single repo. None = search every indexed repo.")
    language: Optional[str] = Field(None, description="Optional language hint: python, typescript, go, java, rust …")
    max_results: int = Field(10, ge=1, le=50)


class SymbolHit(BaseModel):
    symbol: str
    repo: str
    file: str
    language: str = ""
    kind: str = "function"
    matched_line: str = ""
    line_offset_in_chunk: int = 0
    snippet: str = ""
    relevance: float = 0.0


@router.post("/find", response_model=list[SymbolHit])
async def find_symbol_endpoint(req: FindSymbolRequest):
    """Locate the definition of a symbol by name.

    Combines semantic retrieval (good at intent) with a regex pass over the
    matched chunks (good at exact symbol names). Returns the file, the
    matched definition line, a short surrounding snippet, and the relevance
    score from retrieval.
    """
    return find_symbol(
        symbol=req.symbol,
        repo_name=req.repo_name,
        language=req.language,
        max_results=req.max_results,
    )


# ── Caller search ──

class FindCallersRequest(BaseModel):
    symbol: str = Field(..., description="Symbol whose call sites you want to find")
    repo_name: Optional[str] = Field(None)
    max_results: int = Field(20, ge=1, le=50)


@router.post("/callers")
async def find_callers_endpoint(req: FindCallersRequest):
    """Find places that call (use) a symbol — heuristic regex over chunks."""
    return find_callers(
        symbol=req.symbol,
        repo_name=req.repo_name,
        max_results=req.max_results,
    )


# ── Code rewriter ──

class RewriteRequest(BaseModel):
    snippet: str = Field(..., description="The code to rewrite")
    instruction: str = Field(..., description="What change to apply, e.g. 'convert to async', 'add type hints', 'extract validation into a helper'")
    language: Optional[str] = Field(None, description="Language id for the fenced block (python, typescript, …)")
    repo_name: Optional[str] = Field(None, description="If provided, the rewriter pulls similar chunks from this repo so the rewrite stays idiomatic")
    file_path: Optional[str] = Field(None, description="Original file path for the LLM's context")
    use_context: bool = Field(True, description="Whether to ground the rewrite in repo context")
    model: Optional[str] = Field(None, description="Override the LLM model")


class RewriteResponse(BaseModel):
    rewritten_code: str
    rationale: str = ""
    risks: str = ""
    model: str = ""
    context_used: int = 0
    context_sources: list[str] = []
    duration_ms: int = 0
    tokens: int = 0


@router.post("/rewrite", response_model=RewriteResponse)
async def rewrite_endpoint(
    req: RewriteRequest,
    current_user: Optional[dict] = Depends(get_current_user_optional),
):
    """Rewrite a code snippet per the given instruction.

    Returns the new code in ``rewritten_code`` (clean, no fences) plus a
    one-line rationale and a risks line listing things a reviewer should
    double-check.
    """
    return rewrite_snippet(
        snippet=req.snippet,
        instruction=req.instruction,
        language=req.language,
        repo_name=req.repo_name,
        file_path=req.file_path,
        use_context=req.use_context,
        model=req.model,
    )


# ── Snippet explainer (standalone, not tied to IDE state) ──

class ExplainSnippetRequest(BaseModel):
    snippet: str
    language: Optional[str] = None
    repo_name: Optional[str] = None
    file_path: Optional[str] = None
    model: Optional[str] = None


@router.post("/explain")
async def explain_endpoint(req: ExplainSnippetRequest):
    """Return a plain-language explanation of a snippet.

    If ``repo_name`` is provided, the response is grounded in similar code
    from that repository so the explanation can reference real callers and
    helpers by file path.
    """
    return explain_snippet(
        snippet=req.snippet,
        language=req.language,
        repo_name=req.repo_name,
        file_path=req.file_path,
        model=req.model,
    )


# ── Explicit cross-repo comparison query ──

class CrossRepoRequest(BaseModel):
    query: str = Field(..., description="Question to ask across repos — comparison-style works best")
    repo_names: list[str] = Field(..., min_length=2, description="Two or more repos to compare")
    use_dual_model: Optional[bool] = Field(False, description="Deep reasoning mode")
    top_k: Optional[int] = Field(None)


@router.post("/cross-repo")
async def cross_repo_query(
    req: CrossRepoRequest,
    current_user: Optional[dict] = Depends(get_current_user_optional),
):
    """Force a query to run across an explicit list of repos and return a
    comparison-focused answer.

    Use this when you want a side-by-side answer ("How does each of these
    repos handle authentication?"). The plain ``/api/query/all`` endpoint
    answers across all indexed repos but doesn't enforce comparison framing.
    """
    framed = (
        f"Compare and contrast across the following repositories: "
        f"{', '.join(req.repo_names)}. "
        f"For each repo, answer the user's question and call out differences explicitly. "
        f"Question: {req.query}"
    )
    login = (current_user or {}).get("login", "anonymous")
    return ask_all(
        query=framed,
        repo_names=req.repo_names,
        use_hybrid=True,
        use_dual_model=req.use_dual_model,
        top_k=req.top_k,
        user_login=login,
        interface="api",
    )
