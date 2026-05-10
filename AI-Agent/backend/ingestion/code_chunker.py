"""
Function/class-aware code chunker.

The naïve word-count chunker slices functions mid-body, which destroys
refactoring/reasoning quality — the LLM never sees a complete function as
one retrieval unit. This module fixes that.

Strategy by language:
  - Python   → real AST parsing (built-in `ast` — no extra deps)
  - JS / TS  → regex-based function/class detection
  - Java/C#  → regex
  - Go       → regex
  - Rust     → regex
  - Other    → caller falls back to word-count chunker

Each emitted chunk is a *complete* logical unit: one function, one class,
one method. Module-level setup (imports, constants) is bundled into a single
"preamble" chunk so it stays attached to file context.
"""
from __future__ import annotations
import ast
import re
from typing import Optional


# ── Python: real AST ──

def _python_chunks(source: str) -> list[dict]:
    """Return a list of {kind, name, start, end, text} for top-level funcs/classes
    AND class methods, plus a 'preamble' chunk for everything else.

    Methods are emitted with the qualified name ``ClassName.method_name`` so the
    Code Refactor UI can show and target them individually.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    lines = source.splitlines(keepends=True)
    spans: list[dict] = []

    # Track which line ranges are owned by a top-level definition
    consumed: set[int] = set()

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start = (node.decorator_list[0].lineno - 1) if node.decorator_list else (node.lineno - 1)
            end = getattr(node, "end_lineno", node.lineno) or node.lineno
            text = "".join(lines[start:end])
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            spans.append({"kind": kind, "name": node.name, "start": start, "end": end, "text": text})
            for ln in range(start, end):
                consumed.add(ln)

            # If it's a class, also emit its methods as their own selectable spans.
            # The class span still exists (so "refactor whole class" works), but
            # the FE tree gets one entry per method too.
            if isinstance(node, ast.ClassDef):
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        m_start = (sub.decorator_list[0].lineno - 1) if sub.decorator_list else (sub.lineno - 1)
                        m_end = getattr(sub, "end_lineno", sub.lineno) or sub.lineno
                        m_text = "".join(lines[m_start:m_end])
                        spans.append({
                            "kind": "function",
                            "name": f"{node.name}.{sub.name}",
                            "start": m_start,
                            "end": m_end,
                            "text": m_text,
                        })

    # Preamble: everything not owned by a top-level def — module docstring,
    # imports, constants. Concatenated as ONE chunk.
    preamble_lines = [lines[i] for i in range(len(lines)) if i not in consumed]
    preamble = "".join(preamble_lines).strip()
    if preamble:
        spans.insert(0, {"kind": "module", "name": "<preamble>", "start": 0,
                         "end": len(preamble_lines), "text": preamble})

    return spans


# ── Generic: regex-based for non-Python languages ──
#
# Regexes match the START of a function/class. We then walk forward,
# tracking brace depth, to find the matching close.

_LANG_REGEXES: dict[str, list[re.Pattern]] = {
    # JavaScript / TypeScript
    "javascript": [
        re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)", re.MULTILINE),
        re.compile(r"^\s*(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>", re.MULTILINE),
        re.compile(r"^\s*(?:export\s+)?(?:abstract\s+)?class\s+(\w+)", re.MULTILINE),
    ],
    # Java / C#
    "java": [
        re.compile(r"^\s*(?:public|private|protected|static|final|abstract|\s)*\s+(?:[\w<>,\[\]\s]+\s+)?(\w+)\s*\([^)]*\)\s*(?:throws[\w\s,.]+)?\s*\{", re.MULTILINE),
        re.compile(r"^\s*(?:public|private|abstract\s+)?(?:class|interface|enum)\s+(\w+)", re.MULTILINE),
    ],
    # Go
    "go": [
        re.compile(r"^func\s+(\w+)\s*\(", re.MULTILINE),
        re.compile(r"^func\s+\([^)]+\)\s+(\w+)\s*\(", re.MULTILINE),
        re.compile(r"^type\s+(\w+)\s+(?:struct|interface)\s*\{", re.MULTILINE),
    ],
    # Rust
    "rust": [
        re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)\s*[\(<]", re.MULTILINE),
        re.compile(r"^\s*(?:pub\s+)?(?:struct|enum|trait)\s+(\w+)", re.MULTILINE),
        re.compile(r"^\s*impl(?:<[^>]+>)?\s+(?:\w+\s+for\s+)?(\w+)", re.MULTILINE),
    ],
}

# Language families so several extensions share one regex set
_EXT_TO_LANG_FAMILY = {
    ".js": "javascript", ".jsx": "javascript",
    ".ts": "javascript", ".tsx": "javascript",
    ".java": "java", ".cs": "java", ".cpp": "java", ".c": "java", ".h": "java",
    ".go": "go",
    ".rs": "rust",
}


def _find_block_end(source: str, open_pos: int) -> int:
    """Walk forward from ``open_pos`` and return the index just after the
    matching closing brace. Crude but works for non-crazy code: ignores braces
    inside string literals (single, double, backtick) and line comments.
    """
    n = len(source)
    # Find the first '{'
    i = source.find("{", open_pos)
    if i == -1:
        return min(n, open_pos + 800)   # fallback: ~800-char window
    depth = 0
    in_string: Optional[str] = None
    escape = False
    line_comment = False

    while i < n:
        ch = source[i]
        if line_comment:
            if ch == "\n":
                line_comment = False
        elif in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == in_string:
                in_string = None
        else:
            if ch == "/" and i + 1 < n and source[i + 1] == "/":
                line_comment = True
            elif ch in ("'", '"', "`"):
                in_string = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return i + 1
        i += 1
    return n


def _regex_chunks(source: str, family: str) -> list[dict]:
    patterns = _LANG_REGEXES.get(family, [])
    if not patterns:
        return []

    spans: list[dict] = []
    consumed: list[tuple[int, int]] = []   # list of (start, end) char ranges

    def overlaps(start: int, end: int) -> bool:
        for s, e in consumed:
            if start < e and end > s:
                return True
        return False

    for pattern in patterns:
        for m in pattern.finditer(source):
            block_end = _find_block_end(source, m.start())
            # Many regexes match definitions too — for class/struct/enum,
            # we still want the body inside the block.
            start = m.start()
            end = block_end
            if overlaps(start, end):
                continue
            text = source[start:end].strip()
            if not text:
                continue
            name = m.group(1) if m.lastindex else "<anon>"
            kind = "class" if any(k in m.group(0).lower() for k in ("class", "struct", "enum", "interface", "trait", "impl")) else "function"
            spans.append({"kind": kind, "name": name, "start": start, "end": end, "text": text})
            consumed.append((start, end))

    # Preamble — anything before the first def + anything between defs
    if spans:
        spans.sort(key=lambda s: s["start"])
        preamble_parts: list[str] = []
        cursor = 0
        for s in spans:
            if s["start"] > cursor:
                preamble_parts.append(source[cursor:s["start"]])
            cursor = s["end"]
        # tail
        if cursor < len(source):
            preamble_parts.append(source[cursor:])
        preamble = "".join(preamble_parts).strip()
        if preamble:
            spans.insert(0, {"kind": "module", "name": "<preamble>",
                             "start": 0, "end": len(preamble), "text": preamble})
    return spans


# ── Public entry point ──

def function_aware_chunks(content: str, extension: str) -> Optional[list[dict]]:
    """Return function/class-aware chunks for a code file, or ``None`` if the
    language is unsupported (caller should fall back to word-count chunking).

    Each chunk is a dict: ``{kind, name, start, end, text}`` where:
      - ``kind``  ∈ {"function", "class", "module"}
      - ``name``  e.g. "ingest_repository" or "<preamble>"
      - ``text``  the complete source for that unit
    """
    ext = (extension or "").lower()
    if ext == ".py":
        spans = _python_chunks(content)
        return spans or None

    family = _EXT_TO_LANG_FAMILY.get(ext)
    if not family:
        return None

    spans = _regex_chunks(content, family)
    return spans or None
