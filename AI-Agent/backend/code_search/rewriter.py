"""
Code rewriter — accepts a code snippet plus an instruction (e.g. "convert to
async", "add type hints", "extract this into a helper") and returns the
refactored code, optionally grounded in repo context so the rewrite stays
consistent with the surrounding codebase style.
"""
from __future__ import annotations
import re
from typing import Optional

from backend.rag.retriever import hybrid_retrieve
from backend.rag.generator import _call_ollama_chat
from backend.config import settings


REWRITE_SYSTEM_PROMPT = """You are an expert software engineer performing a careful code rewrite.

You receive:
  1. A snippet of code to transform.
  2. An instruction describing the desired change.
  3. (Optional) Context chunks from the surrounding repository so your rewrite
     stays consistent with existing patterns, helpers, and conventions.

Output rules — STRICT:
  - Return ONLY a fenced code block with the rewritten code.
  - Then a single line beginning with "Rationale:" summarising what changed (≤ 30 words).
  - Then a single line beginning with "Risks:" listing anything the human reviewer should double-check (≤ 30 words). If none, write "Risks: none.".
  - Do NOT include the original code, do NOT prepend imports the user already had unless adding them is part of the rewrite, do NOT wrap in extra commentary.
  - Preserve the language of the original snippet.
  - Match the indentation and quoting style of the surrounding repo when context is provided."""


_FENCE_RE = re.compile(r"```(?:\w+)?\n(.*?)```", re.DOTALL)


def _extract_code_and_meta(raw: str) -> dict:
    """Pull the first fenced block out of the LLM output, plus rationale/risks."""
    code_match = _FENCE_RE.search(raw)
    code = code_match.group(1).rstrip() if code_match else raw.strip()

    rationale = ""
    risks = ""
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("rationale:"):
            rationale = stripped.split(":", 1)[1].strip()
        elif stripped.lower().startswith("risks:"):
            risks = stripped.split(":", 1)[1].strip()
    return {"code": code, "rationale": rationale, "risks": risks}


def rewrite_snippet(
    snippet: str,
    instruction: str,
    language: Optional[str] = None,
    repo_name: Optional[str] = None,
    file_path: Optional[str] = None,
    use_context: bool = True,
    model: Optional[str] = None,
) -> dict:
    """Rewrite a snippet according to ``instruction``.

    If ``repo_name`` is given and ``use_context`` is True, the rewriter pulls
    the most-similar 5 chunks from that repo and prepends them as context so
    the new code stays idiomatic to the repo.

    Returns:
        {
            "rewritten_code": str,
            "rationale": str,
            "risks": str,
            "model": str,
            "context_used": int,        # number of chunks
            "context_sources": list,    # file paths
        }
    """
    if not snippet or not snippet.strip():
        raise ValueError("snippet cannot be empty")
    if not instruction or not instruction.strip():
        raise ValueError("instruction cannot be empty")

    # Optional repo context
    context_block = ""
    context_sources: list[str] = []
    if use_context and repo_name:
        try:
            chunks = hybrid_retrieve(
                f"{instruction}\n\n{snippet[:300]}",
                repo_name,
                top_k=5,
                chunk_types=["code"],
            )
            if chunks:
                context_block = "\n\n--- Repo context (read-only, for style/consistency) ---\n"
                for c in chunks:
                    src = c.get("source", "unknown")
                    context_sources.append(src)
                    context_block += f"\n[{src}]\n{c['text'][:1200]}\n"
        except Exception as e:
            print(f"   ⚠️ Could not fetch repo context for rewrite: {e}")

    fence = language or ""
    user_prompt = (
        f"Instruction: {instruction.strip()}\n\n"
        f"{('File: ' + file_path) if file_path else ''}\n"
        f"Original snippet:\n```{fence}\n{snippet}\n```"
        f"{context_block}"
    )

    chat = _call_ollama_chat(
        model=model or settings.LLM_MODEL,
        messages=[
            {"role": "system", "content": REWRITE_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
        context_window=settings.LLM_CONTEXT_WINDOW,
    )

    parsed = _extract_code_and_meta(chat.get("content", ""))
    return {
        "rewritten_code": parsed["code"],
        "rationale": parsed["rationale"],
        "risks": parsed["risks"],
        "model": model or settings.LLM_MODEL,
        "context_used": len(context_sources),
        "context_sources": list(dict.fromkeys(context_sources)),  # dedupe, preserve order
        "duration_ms": chat.get("duration_ms", 0),
        "tokens": chat.get("tokens", 0),
    }


def explain_snippet(
    snippet: str,
    language: Optional[str] = None,
    repo_name: Optional[str] = None,
    file_path: Optional[str] = None,
    model: Optional[str] = None,
) -> dict:
    """Return a step-by-step plain-language explanation of a code snippet."""
    if not snippet or not snippet.strip():
        raise ValueError("snippet cannot be empty")

    fence = language or ""
    context_block = ""
    context_sources: list[str] = []
    if repo_name:
        try:
            chunks = hybrid_retrieve(snippet[:300], repo_name, top_k=4, chunk_types=["code"])
            if chunks:
                context_block = "\n\n--- Related repo context ---\n"
                for c in chunks:
                    src = c.get("source", "unknown")
                    context_sources.append(src)
                    context_block += f"\n[{src}]\n{c['text'][:800]}\n"
        except Exception as e:
            print(f"   ⚠️ Could not fetch context for explain: {e}")

    user_prompt = (
        f"Explain the snippet below for an experienced developer who is new to "
        f"this codebase. Cover: what it does, key control flow, any non-obvious "
        f"calls or invariants, and how it relates to the surrounding code if "
        f"context is provided.\n\n"
        f"{('File: ' + file_path) if file_path else ''}\n"
        f"```{fence}\n{snippet}\n```"
        f"{context_block}"
    )

    chat = _call_ollama_chat(
        model=model or settings.LLM_MODEL,
        messages=[
            {"role": "system", "content": (
                "You are an expert code reviewer explaining code clearly and concisely. "
                "Use short paragraphs and bullet points. Cite the file paths from any provided context.")},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
        context_window=settings.LLM_CONTEXT_WINDOW,
    )

    return {
        "explanation": chat.get("content", "").strip(),
        "model": model or settings.LLM_MODEL,
        "context_sources": list(dict.fromkeys(context_sources)),
        "duration_ms": chat.get("duration_ms", 0),
    }
