"""
Context Builder — Structures retrieved chunks into a rich LLM prompt context.
- Groups chunks by source file for readability
- Adds file-level and type-level section headers
- Injects reasoning hints based on chunk composition
- Keeps prompt within a target token budget
"""
from __future__ import annotations
from collections import defaultdict


# Approximate chars-per-token for budget estimation
CHARS_PER_TOKEN = 4


def build_context_block(
    chunks: list[dict],
    max_chars: int = 12000,
    include_reasoning_hint: bool = True,
) -> str:
    """Build a structured context block from retrieved chunks.

    Groups by type (code, commit, commit_diff, branch, documentation),
    then within each type groups by source file.

    Returns a formatted string to inject into the LLM prompt.
    """
    if not chunks:
        return "No relevant context found."

    # ── Separate by type ──────────────────────────────────────────────────
    by_type: dict[str, list[dict]] = defaultdict(list)
    for chunk in chunks:
        by_type[chunk.get("type", "code")].append(chunk)

    sections = []
    char_budget = max_chars

    # ── 1. Branches (CRITICAL: Move to top so LLM sees the structure first) ──
    branch_chunks = by_type.get("branch", [])
    if branch_chunks and char_budget > 0:
        sections.append(f"{'=' * 60}\n🌿 AVAILABLE REPOSITORY BRANCHES\n{'=' * 60}")
        # Quick summary for the LLM
        branch_names = [c.get("text", "").split("|")[1].strip() if "|" in c.get("text", "") else "unknown" for c in branch_chunks]
        sections.append(f"Detected Branches: {', '.join(branch_names)}\n")
        
        for c in branch_chunks:
            text = c["text"]
            char_budget -= len(text)
            sections.append(f"\n{text}")
            if char_budget <= 0:
                break

    # ── 2. Commits / Commit Diffs ────────────────────────────────────────────
    for chunk_type, header in [("commit_diff", "🔀 COMMIT DIFFS"), ("commit", "📜 COMMIT HISTORY")]:
        type_chunks = by_type.get(chunk_type, [])
        if not type_chunks or char_budget <= 0:
            continue

        sections.append(f"\n{'─' * 60}\n{header}\n{'─' * 60}")
        for c in type_chunks:
            text = c["text"]
            if len(text) > char_budget:
                text = text[:char_budget] + "\n... [truncated]"
                char_budget = 0
            else:
                char_budget -= len(text)
            sections.append(f"\n{text}")
            if char_budget <= 0:
                break

    # ── 3. Code / Documentation ──────────────────────────────────────────────
    for chunk_type in ["code", "documentation"]:
        type_chunks = by_type.get(chunk_type, [])
        if not type_chunks or char_budget <= 0:
            continue

        header = "📄 SOURCE CODE" if chunk_type == "code" else "📚 DOCUMENTATION"
        sections.append(f"\n{'─' * 60}\n{header}\n{'─' * 60}")

        # Group by file path
        by_file: dict[str, list[dict]] = defaultdict(list)
        for c in type_chunks:
            by_file[c.get("source", "unknown")].append(c)

        for file_path, file_chunks in by_file.items():
            lang = file_chunks[0].get("language", "")
            # Include branch info in file header if available
            branch_info = f" [Branch: {file_chunks[0].get('branch', 'unknown')}]" if file_chunks[0].get('branch') else ""
            sections.append(f"\n### {file_path}{branch_info} ({lang})")
            
            for c in file_chunks:
                text = c["text"]
                if len(text) > char_budget:
                    text = text[:char_budget] + "\n... [truncated]"
                    char_budget = 0
                else:
                    char_budget -= len(text)
                sections.append(text)
                if char_budget <= 0:
                    sections.append("... [context budget exceeded]")
                    break
            if char_budget <= 0:
                break

    context_block = "\n".join(sections)

    # ── Reasoning Hint ────────────────────────────────────────────────────
    if include_reasoning_hint:
        hint = _build_reasoning_hint(by_type)
        if hint:
            context_block = f"{hint}\n\n{context_block}"

    return context_block


def _build_reasoning_hint(by_type: dict[str, list]) -> str:
    """Generate a reasoning hint telling the LLM what kinds of context it has."""
    parts = []
    if by_type.get("code") or by_type.get("documentation"):
        n = len(by_type.get("code", [])) + len(by_type.get("documentation", []))
        parts.append(f"{n} source file chunk(s)")
    if by_type.get("commit_diff"):
        n = len(by_type["commit_diff"])
        parts.append(f"{n} commit diff(s) showing what changed")
    if by_type.get("commit"):
        n = len(by_type["commit"])
        parts.append(f"{n} commit message(s)")
    if by_type.get("branch"):
        n = len(by_type["branch"])
        parts.append(f"{n} branch record(s)")

    if not parts:
        return ""

    summary = ", ".join(parts)
    return (
        f"[CONTEXT SUMMARY: Retrieved {summary}. "
        f"Use commit SHA hashes and branch names in your answer where relevant. "
        f"Cite sources using [filename] notation.]"
    )


def get_chunk_type_summary(chunks: list[dict]) -> str:
    """Return a brief summary string of chunk types for the system prompt."""
    counts: dict[str, int] = defaultdict(int)
    for c in chunks:
        counts[c.get("type", "code")] += 1

    parts = []
    for t, n in counts.items():
        parts.append(f"{n} {t}")
    return ", ".join(parts) if parts else "code"
