"""
Symbol finder — locate function/class/method definitions across one or many
repos by combining semantic retrieval (good at intent) with regex matching on
the chunk text (good at exact symbol names).

Definitions tracked:
  - Python:        ``def name(`` and ``class Name(``
  - JS/TS:         ``function name(``, ``const name = (``, ``class Name``,
                   ``export function name``, ``name: (`` (object methods)
  - Java/C#/C++:   ``returntype name(`` (heuristic) and ``class Name``
  - Go:            ``func name(`` and ``func (r Recv) name(``
  - Rust:          ``fn name(`` and ``impl ... { fn name(``

Returned chunks include the exact line where the definition lives plus a
short surrounding window from the chunk itself.
"""
from __future__ import annotations
import re
from typing import Optional

from backend.rag.retriever import hybrid_retrieve, hybrid_retrieve_multi_repo
from backend.ingestion.embedder import list_collections


# ── Definition-line regexes by language family ──

_DEF_PATTERNS: dict[str, list[str]] = {
    "python": [
        r"^\s*(?:async\s+)?def\s+{name}\s*\(",
        r"^\s*class\s+{name}\b",
    ],
    "javascript": [
        r"^\s*(?:export\s+)?(?:async\s+)?function\s+{name}\s*\(",
        r"^\s*(?:export\s+)?const\s+{name}\s*=\s*(?:async\s*)?\(",
        r"^\s*(?:export\s+)?class\s+{name}\b",
        r"^\s*{name}\s*[:=]\s*(?:async\s*)?\(",   # method shorthand
    ],
    "typescript": "javascript",   # alias
    "tsx": "javascript",
    "jsx": "javascript",
    "java": [
        r"^\s*(?:public|private|protected|static|final|\s)*\s+\w[\w<>,\s]*\s+{name}\s*\(",
        r"^\s*(?:public|private|abstract\s+)?class\s+{name}\b",
        r"^\s*interface\s+{name}\b",
    ],
    "go": [
        r"^\s*func\s+{name}\s*\(",
        r"^\s*func\s+\([^)]+\)\s+{name}\s*\(",
        r"^\s*type\s+{name}\s+(?:struct|interface)\b",
    ],
    "rust": [
        r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+{name}\s*\(",
        r"^\s*(?:pub\s+)?struct\s+{name}\b",
        r"^\s*(?:pub\s+)?enum\s+{name}\b",
        r"^\s*impl\s+(?:\w+\s+for\s+)?{name}\b",
    ],
    "cpp":  ["__java__"],
    "c":    ["__java__"],
}


def _patterns_for(language: str) -> list[str]:
    """Resolve language → regex template list (handling aliases)."""
    lang = (language or "").lower()
    pats = _DEF_PATTERNS.get(lang, [])
    if isinstance(pats, str):
        pats = _DEF_PATTERNS.get(pats, [])
    if pats == ["__java__"]:
        pats = _DEF_PATTERNS["java"]
    if not pats:
        # Fallback: try every language family
        pats = sum(
            (p for p in _DEF_PATTERNS.values() if isinstance(p, list) and p != ["__java__"]),
            [],
        )
    return pats


def _scan_chunk_for_def(chunk_text: str, symbol: str, language: str) -> Optional[dict]:
    """Return the definition match in a chunk if any.

    The match contains:
      - matched_line: the exact line of the definition
      - line_offset: 0-based line number within the chunk
      - kind: "function" | "class" | "method" | "interface" | "type"
      - snippet: line-range surrounding the match (~20 lines)
    """
    safe = re.escape(symbol)
    patterns = _patterns_for(language)

    lines = chunk_text.splitlines()
    for i, line in enumerate(lines):
        for tpl in patterns:
            try:
                rx = re.compile(tpl.format(name=safe))
            except re.error:
                continue
            if rx.search(line):
                # Heuristic kind from the matched line
                lower = line.strip().lower()
                if "class" in lower:
                    kind = "class"
                elif "interface" in lower:
                    kind = "interface"
                elif "type" in lower and "struct" in lower:
                    kind = "struct"
                elif "type" in lower:
                    kind = "type"
                elif "impl" in lower:
                    kind = "impl"
                else:
                    kind = "function"
                start = max(0, i - 5)
                end = min(len(lines), i + 25)
                snippet = "\n".join(lines[start:end])
                return {
                    "matched_line": line.rstrip(),
                    "line_offset": i,
                    "kind": kind,
                    "snippet": snippet,
                }
    return None


def find_symbol(
    symbol: str,
    repo_name: Optional[str] = None,
    language: Optional[str] = None,
    top_k: int = 20,
    max_results: int = 10,
) -> list[dict]:
    """Find a symbol's definition(s) across one or all repos.

    Args:
        symbol: function/class/method name (no parentheses)
        repo_name: scope to one repo, or ``None`` for every indexed repo
        language: optional hint (``python``, ``typescript``, ``go``, …) — speeds
                  up matching by skipping unrelated regex families
        top_k: how many chunks the retriever should fetch before regex filtering
        max_results: cap on the returned hits

    Returns:
        List of hits, each containing repo, file path, kind, line, snippet,
        and the underlying chunk relevance score.
    """
    if not symbol or not symbol.strip():
        return []
    symbol = symbol.strip()

    # 1. Retrieve candidate chunks via hybrid search
    if repo_name:
        candidates = hybrid_retrieve(symbol, repo_name, top_k=top_k, chunk_types=["code"])
    else:
        repos = list_collections()
        if not repos:
            return []
        candidates = hybrid_retrieve_multi_repo(symbol, repos, top_k=top_k, chunk_types=["code"])

    # 2. Regex-scan each chunk for the actual definition
    hits: list[dict] = []
    for c in candidates:
        lang = language or c.get("language", "") or ""
        match = _scan_chunk_for_def(c["text"], symbol, lang)
        if match is None:
            continue
        hits.append({
            "symbol": symbol,
            "repo": c.get("repo", repo_name or "unknown"),
            "file": c.get("source", "unknown"),
            "language": c.get("language", lang),
            "kind": match["kind"],
            "matched_line": match["matched_line"],
            "line_offset_in_chunk": match["line_offset"],
            "snippet": match["snippet"],
            "relevance": c.get("relevance", 0.0),
        })

    # 3. Dedupe by (repo, file, matched_line) and trim
    seen = set()
    unique: list[dict] = []
    for h in hits:
        key = (h["repo"], h["file"], h["matched_line"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(h)

    return unique[:max_results]


def find_callers(symbol: str, repo_name: Optional[str] = None, top_k: int = 30, max_results: int = 20) -> list[dict]:
    """Find chunks that *call* a symbol (rough heuristic — pattern ``symbol(``).

    Useful for "where is this function used?" questions.
    """
    if not symbol or not symbol.strip():
        return []
    symbol = symbol.strip()

    if repo_name:
        candidates = hybrid_retrieve(f"{symbol} usage", repo_name, top_k=top_k, chunk_types=["code"])
    else:
        repos = list_collections()
        candidates = hybrid_retrieve_multi_repo(f"{symbol} usage", repos, top_k=top_k, chunk_types=["code"])

    rx = re.compile(rf"\b{re.escape(symbol)}\s*\(")
    callers: list[dict] = []
    for c in candidates:
        # Skip the definition file/chunk itself
        defn = _scan_chunk_for_def(c["text"], symbol, c.get("language", ""))
        if defn:
            continue
        for i, line in enumerate(c["text"].splitlines()):
            if rx.search(line):
                start = max(0, i - 2)
                end = min(len(c["text"].splitlines()), i + 4)
                snippet = "\n".join(c["text"].splitlines()[start:end])
                callers.append({
                    "symbol": symbol,
                    "repo": c.get("repo", repo_name or "unknown"),
                    "file": c.get("source", "unknown"),
                    "language": c.get("language", ""),
                    "matched_line": line.rstrip(),
                    "snippet": snippet,
                    "relevance": c.get("relevance", 0.0),
                })
                break
        if len(callers) >= max_results:
            break

    return callers[:max_results]
