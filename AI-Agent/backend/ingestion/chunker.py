"""
Smart Text Chunker — Mature Version
- Contextual chunking: file path + language prefix prepended to every chunk
- Larger overlap (128 words) to avoid context loss at boundaries
- Richer metadata: language, file_path, total_chunks, char_offset
- New chunk types: branch, commit_diff
"""
from __future__ import annotations
from typing import Optional

# Maps file extensions to display language names
LANGUAGE_MAP = {
    ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
    ".tsx": "TypeScript/React", ".jsx": "JavaScript/React",
    ".java": "Java", ".go": "Go", ".rs": "Rust",
    ".cpp": "C++", ".c": "C", ".h": "C/C++ Header",
    ".sql": "SQL", ".sh": "Shell", ".md": "Markdown",
    ".yaml": "YAML", ".yml": "YAML", ".json": "JSON",
    ".html": "HTML", ".css": "CSS", ".scss": "SCSS",
}


def chunk_text(text: str, chunk_size: int = 512, overlap: int = 128) -> list[str]:
    """Sliding-window word-based chunker with overlap.
    Caps each chunk at ~5000 chars (safe for nomic-embed-text's 8K token limit).
    """
    MAX_CHUNK_CHARS = 5000

    words = text.split()
    if not words:
        return []

    if len(words) <= chunk_size:
        chunk = text
        return [chunk[:MAX_CHUNK_CHARS]] if chunk.strip() else []

    chunks = []
    step = max(chunk_size - overlap, 1)
    i = 0
    while i < len(words):
        chunk = " ".join(words[i:i + chunk_size])
        if chunk.strip():
            # Hard cap: truncate at char level as a final safety net
            chunks.append(chunk[:MAX_CHUNK_CHARS])
        i += step

    return chunks


def _make_context_prefix(file_dict: dict) -> str:
    """Build a context prefix line to prepend to every chunk from this file.

    E.g.: [Python | src/models/user.py]
    This ensures the LLM always knows where context came from, even without sources.
    """
    ext = file_dict.get("extension", "")
    lang = LANGUAGE_MAP.get(ext, ext.lstrip(".").upper() if ext else "File")
    path = file_dict.get("path", "unknown")
    return f"[{lang} | {path}]\n"


def chunk_file(file_dict: dict, chunk_size: int = 512, overlap: int = 128) -> list[dict]:
    """Chunk a source file and attach rich metadata to each chunk.

    Each chunk gets:
    - A context prefix line with language + file path
    - Metadata: source, type, extension, language, chunk_index, total_chunks, file_size
    """
    prefix = _make_context_prefix(file_dict)
    raw_chunks = chunk_text(file_dict["content"], chunk_size, overlap)
    total = len(raw_chunks)

    ext = file_dict.get("extension", "")
    lang = LANGUAGE_MAP.get(ext, "code")
    is_doc = ext in {".md", ".txt", ".rst"}

    return [
        {
            "text": prefix + chunk,          # context-enriched chunk
            "source": file_dict["path"],
            "type": "documentation" if is_doc else "code",
            "extension": ext,
            "language": lang,
            "chunk_index": idx,
            "total_chunks": total,
            "file_size": file_dict.get("size", 0),
        }
        for idx, chunk in enumerate(raw_chunks)
        if chunk.strip()
    ]


def chunk_commit(commit: dict) -> dict:
    """Format a basic commit (message + files list) as a single indexable chunk."""
    files_str = ", ".join(commit["files_changed"][:10])
    if len(commit["files_changed"]) > 10:
        files_str += f" (+{len(commit['files_changed']) - 10} more)"

    text = (
        f"[Commit | {commit['sha']}]\n"
        f"Author: {commit['author']} on {commit['date']}\n"
        f"Message: {commit['message']}\n"
        f"Files changed: {files_str}"
    )

    return {
        "text": text,
        "source": f"commit:{commit['sha']}",
        "type": "commit",
        "sha": commit["sha"],
        "author": commit.get("author", ""),
        "date": commit.get("date", ""),
        "chunk_index": 0,
        "total_chunks": 1,
    }


def chunk_commit_diff(commit: dict) -> dict:
    """Format a commit WITH its diff patch — much richer than files-only.

    The diff shows exactly what changed, enabling 'why was this changed?' reasoning.
    """
    files_str = ", ".join(commit.get("files_changed", [])[:10])
    diff_text = commit.get("diff", "")

    # Truncate diff to fit embedding window reasonably
    if len(diff_text) > 2000:
        diff_text = diff_text[:2000] + "\n... [diff truncated]"

    text = (
        f"[Commit Diff | {commit.get('sha', 'unknown')}]\n"
        f"Author: {commit.get('author', '')} on {commit.get('date', '')}\n"
        f"Message: {commit.get('message', '').strip()}\n"
        f"Files changed: {files_str}\n"
        f"\nDiff:\n{diff_text}"
    )

    return {
        "text": text,
        "source": f"commit:{commit.get('sha', 'unknown')}",
        "type": "commit_diff",
        "sha": commit.get("sha", ""),
        "author": commit.get("author", ""),
        "date": commit.get("date", ""),
        "chunk_index": 0,
        "total_chunks": 1,
    }


def chunk_branch(branch: dict) -> dict:
    """Format a git branch as a searchable chunk.

    Enables queries like 'what branches exist?' or 'what was worked on in feature/auth?'
    """
    text = (
        f"[Branch | {branch['name']}]\n"
        f"Last commit: {branch.get('last_commit_sha', 'unknown')[:8]} "
        f"by {branch.get('last_commit_author', 'unknown')} "
        f"on {branch.get('last_commit_date', 'unknown')}\n"
        f"Message: {branch.get('last_commit_message', '').strip()}\n"
        f"Merged: {branch.get('merged', 'unknown')}\n"
        f"Is default: {branch.get('is_default', False)}"
    )

    return {
        "text": text,
        "source": f"branch:{branch['name']}",
        "type": "branch",
        "branch_name": branch["name"],
        "is_default": str(branch.get("is_default", False)),
        "merged": str(branch.get("merged", False)),
        "chunk_index": 0,
        "total_chunks": 1,
    }


def chunk_issue_or_pr(item: dict, item_type: str = "issue") -> list[dict]:
    """Chunk a GitHub issue or pull request."""
    text = (
        f"[{item_type.title()} #{item.get('number', '?')} | {item.get('title', '')}]\n"
        f"State: {item.get('state', 'unknown')}\n"
        f"Author: {item.get('author', 'unknown')}\n"
        f"\n{item.get('body', '')}"
    )

    chunks = chunk_text(text, chunk_size=512, overlap=128)
    return [
        {
            "text": chunk,
            "source": f"{item_type}:#{item.get('number', '?')}",
            "type": item_type,
            "chunk_index": idx,
            "total_chunks": len(chunks),
        }
        for idx, chunk in enumerate(chunks)
        if chunk.strip()
    ]
