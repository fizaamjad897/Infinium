"""
Granular code refactor — function-level or whole-file.

  refactor_function(repo_name, target, instruction)  → diff + before/after for a single symbol
  refactor_file(repo_name, target_path, instruction) → diff + before/after for a whole file

Both are READ-ONLY: nothing is written to disk or pushed to GitHub. The caller
(typically a UI) shows the diff and lets the human apply it themselves.
"""
from __future__ import annotations
import os
import re
import time
import difflib
from typing import Optional

from backend.code_search.symbol_finder import find_symbol, find_callers
from backend.rag.retriever import hybrid_retrieve
from backend.rag.generator import _call_ollama_chat
from backend.feedback.memory import recall as _recall_feedback, render_for_prompt as _render_feedback
from backend.config import settings


REFACTOR_SYSTEM_PROMPT = """You are an expert software engineer performing a careful, surgical refactor.

You receive:
  1. The current code (a function/class OR a whole file).
  2. An instruction describing the desired change.
  3. (Optional) Context — callers, neighbour files, repo conventions.

Output format — STRICT, do NOT deviate:
  ```<language>
  <full rewritten code, ready to paste in place of the original>
  ```
  Rationale: <≤ 40 words on what changed and why>
  Risks: <≤ 40 words on what a reviewer should double-check, or "none">

Rules:
  - Preserve the original public signature/exports unless the instruction says otherwise.
  - Match the indentation, quoting, and import style of the original code.
  - Do NOT include the original code, do NOT add explanatory prose outside the
    Rationale and Risks lines, do NOT wrap the response in extra commentary.
  - If the instruction is ambiguous or unsafe, return the original code unchanged
    inside the fence and explain why in the Rationale line."""


_FENCE_RE = re.compile(r"```(?:\w+)?\n(.*?)```", re.DOTALL)


def _parse_response(raw: str, original: str) -> dict:
    """Extract the rewritten code, rationale, and risks from the LLM output."""
    code_match = _FENCE_RE.search(raw)
    new_code = code_match.group(1).rstrip() if code_match else original

    rationale = ""
    risks = ""
    for line in raw.splitlines():
        s = line.strip()
        if s.lower().startswith("rationale:"):
            rationale = s.split(":", 1)[1].strip()
        elif s.lower().startswith("risks:"):
            risks = s.split(":", 1)[1].strip()
    return {"new_code": new_code, "rationale": rationale, "risks": risks}


def _make_diff(before: str, after: str, path_label: str) -> str:
    """Unified diff string suitable for display (or `git apply`)."""
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path_label}",
            tofile=f"b/{path_label}",
            n=3,
        )
    )


def _read_file_from_cache(repo_name: str, file_path: str) -> Optional[str]:
    """Look up the cloned-repo content of a file. Returns None if not on disk."""
    cache_dir = os.path.join(settings.REPO_CACHE_DIR, repo_name.replace("/", "_"))
    candidate = os.path.join(cache_dir, file_path)
    if not os.path.isfile(candidate):
        return None
    try:
        with open(candidate, "r", encoding="utf-8", errors="ignore") as fh:
            return fh.read()
    except Exception:
        return None


def _extract_symbol_from_disk(repo_name: str, file_path: str, symbol_name: str) -> Optional[dict]:
    """Read ``file_path`` from ``repo_cache/<repo>/`` and AST-extract the named
    function/class. Works for files that were skipped at ingest time (since
    we read the disk, not the index)."""
    from backend.ingestion.code_chunker import function_aware_chunks
    content = _read_file_from_cache(repo_name, file_path)
    if content is None:
        return None
    ext = os.path.splitext(file_path)[1].lower()
    spans = function_aware_chunks(content, ext) or []
    bare = symbol_name.split(".")[-1].strip()
    for s in spans:
        if s.get("name") == bare and s.get("kind") in ("function", "class"):
            return {
                "name": s["name"],
                "kind": s["kind"],
                "text": s["text"],
                "start": s.get("start", 0),
                "end": s.get("end", 0),
                "language": ext.lstrip("."),
            }
    return None


def refactor_function(
    repo_name: str,
    target: str,
    instruction: str,
    model: Optional[str] = None,
    include_callers: bool = True,
    file: Optional[str] = None,
    code: Optional[str] = None,
) -> dict:
    """Refactor a single function/class.

    Resolution priority:
      1. If ``code`` is provided → use it as-is (FE sent the exact span).
      2. If ``file`` is provided → read it from ``repo_cache/`` and AST-extract
         ``target`` (works even when the file was skipped at ingest time).
      3. Else → fall back to ``symbol_finder`` (ChromaDB-based lookup).

    Returns ``{ "found": False, "reason": "..." }`` when the symbol can't be located.
    """
    if not target.strip() or not instruction.strip():
        raise ValueError("target and instruction are required")

    bare = target.split(".")[-1].strip()
    symbol_code = ""
    file_path = file or ""
    language = ""

    # Path 1: caller supplied the exact code
    if code and code.strip():
        symbol_code = code.strip()
        if file_path:
            language = os.path.splitext(file_path)[1].lstrip(".")
    # Path 2: caller supplied the file → read from disk
    elif file_path:
        extracted = _extract_symbol_from_disk(repo_name, file_path, target)
        if extracted is None:
            return {"found": False,
                    "reason": f"Symbol '{target}' not found in {file_path} (file may not exist on disk)"}
        symbol_code = extracted["text"].strip()
        language = extracted["language"]
    # Path 3: fall back to RAG-based symbol lookup
    else:
        hits = find_symbol(bare, repo_name=repo_name, max_results=3)
        if not hits:
            return {"found": False,
                    "reason": f"Symbol '{target}' not found in {repo_name}. "
                              f"Pass `file` to look it up by path, or `code` to refactor a specific snippet."}
        hit = hits[0]
        symbol_code = hit.get("snippet", "").strip()
        file_path = hit.get("file", "")
        language = hit.get("language", "") or ""

    # Pull callers for context
    caller_lines: list[str] = []
    if include_callers:
        try:
            callers = find_callers(bare, repo_name=repo_name, max_results=5)
            for c in callers:
                caller_lines.append(f"// caller in {c.get('file','?')}:\n{c.get('snippet','')[:400]}")
        except Exception:
            pass

    context_block = "\n\n".join(caller_lines) if caller_lines else ""

    user_prompt = (
        f"FILE: {file_path}\n"
        f"SYMBOL: {target}\n"
        f"INSTRUCTION: {instruction}\n\n"
        f"CURRENT CODE:\n```{language}\n{symbol_code}\n```\n"
    )
    if context_block:
        user_prompt += f"\nCALLER CONTEXT (do not modify, for awareness only):\n{context_block}\n"

    # Pull in past-feedback memory (positive examples to emulate, negative to avoid)
    memory = _recall_feedback(
        instruction, target_type="refactor", repo_name=repo_name,
        k_positive=settings.FEEDBACK_MEMORY_TOP_K_POS,
        k_negative=settings.FEEDBACK_MEMORY_TOP_K_NEG,
    )
    memory_block = _render_feedback(memory)
    system_prompt = REFACTOR_SYSTEM_PROMPT + (("\n\n" + memory_block) if memory_block else "")

    chosen_model = model or settings.CODE_REFACTOR_MODEL
    t0 = time.time()
    result = _call_ollama_chat(
        chosen_model,
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.1,
        context_window=settings.LLM_CONTEXT_WINDOW,
        num_predict=settings.REFACTOR_MAX_TOKENS,
        timeout=settings.REFACTOR_HTTP_TIMEOUT,
    )
    parsed = _parse_response(result.get("content", ""), symbol_code)
    diff = _make_diff(symbol_code, parsed["new_code"], file_path)

    return {
        "found": True,
        "granularity": "function",
        "memory_used": {"positive": len(memory.get("positive") or []),
                         "negative": len(memory.get("negative") or [])},
        "repo_name": repo_name,
        "target": target,
        "file": file_path,
        "language": language,
        "before": symbol_code,
        "after": parsed["new_code"],
        "diff": diff,
        "rationale": parsed["rationale"],
        "risks": parsed["risks"],
        "callers_used": len(caller_lines),
        "model": chosen_model,
        "duration_ms": int((time.time() - t0) * 1000),
        "tokens": result.get("tokens", 0),
        "error": result.get("error"),
    }


def refactor_file(
    repo_name: str,
    target_path: str,
    instruction: str,
    model: Optional[str] = None,
    use_neighbour_context: bool = True,
) -> dict:
    """Refactor a whole file.

    Reads the file from ``repo_cache``, optionally retrieves a few neighbour
    chunks for style context, and asks the model to rewrite the file end-to-end.
    """
    if not target_path.strip() or not instruction.strip():
        raise ValueError("target_path and instruction are required")

    original = _read_file_from_cache(repo_name, target_path)
    if original is None:
        return {"found": False, "reason": f"File '{target_path}' not found in repo_cache for {repo_name}"}

    # Optional: pull a couple of similar/importing chunks for style cues.
    neighbours: list[str] = []
    if use_neighbour_context:
        try:
            chunks = hybrid_retrieve(target_path, repo_name, top_k=4, chunk_types=["code"])
            for c in chunks:
                src = c.get("source", "")
                if src == target_path:
                    continue
                snippet = c.get("text", "")[:600]
                neighbours.append(f"// {src}:\n{snippet}")
        except Exception:
            pass

    ext = os.path.splitext(target_path)[1].lstrip(".") or ""
    user_prompt = (
        f"FILE: {target_path}\n"
        f"INSTRUCTION: {instruction}\n\n"
        f"CURRENT FILE:\n```{ext}\n{original}\n```\n"
    )
    if neighbours:
        user_prompt += (
            "\nNEIGHBOUR FILES (for style/convention reference only — do not modify):\n"
            + "\n\n".join(neighbours[:3])
            + "\n"
        )

    memory = _recall_feedback(
        instruction, target_type="refactor", repo_name=repo_name,
        k_positive=settings.FEEDBACK_MEMORY_TOP_K_POS,
        k_negative=settings.FEEDBACK_MEMORY_TOP_K_NEG,
    )
    memory_block = _render_feedback(memory)
    system_prompt = REFACTOR_SYSTEM_PROMPT + (("\n\n" + memory_block) if memory_block else "")

    chosen_model = model or settings.CODE_REFACTOR_MODEL
    t0 = time.time()
    # File-level rewrites need ~2× tokens vs function-level.
    result = _call_ollama_chat(
        chosen_model,
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.1,
        context_window=max(settings.LLM_CONTEXT_WINDOW, 8192),
        num_predict=settings.REFACTOR_MAX_TOKENS * 2,
        timeout=settings.REFACTOR_HTTP_TIMEOUT,
    )
    parsed = _parse_response(result.get("content", ""), original)
    diff = _make_diff(original, parsed["new_code"], target_path)

    return {
        "found": True,
        "granularity": "file",
        "memory_used": {"positive": len(memory.get("positive") or []),
                         "negative": len(memory.get("negative") or [])},
        "repo_name": repo_name,
        "target": target_path,
        "file": target_path,
        "language": ext,
        "before": original,
        "after": parsed["new_code"],
        "diff": diff,
        "rationale": parsed["rationale"],
        "risks": parsed["risks"],
        "neighbours_used": len(neighbours),
        "model": chosen_model,
        "duration_ms": int((time.time() - t0) * 1000),
        "tokens": result.get("tokens", 0),
        "error": result.get("error"),
    }
