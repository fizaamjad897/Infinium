"""
Smart Text Chunker
- Sliding window with overlap for code/docs
- Preserves commit metadata
- Handles different content types
"""
from typing import Optional


def chunk_text(text: str, chunk_size: int = 512, overlap: int = 64) -> list[str]:
    """Sliding-window word-based chunker.
    
    Args:
        text: The text to chunk
        chunk_size: Number of words per chunk
        overlap: Number of overlapping words between chunks
    """
    words = text.split()
    if not words:
        return []
    
    # If text is shorter than chunk_size, return as-is
    if len(words) <= chunk_size:
        return [text]
    
    chunks = []
    i = 0
    step = max(chunk_size - overlap, 1)
    
    while i < len(words):
        chunk = " ".join(words[i:i + chunk_size])
        if chunk.strip():
            chunks.append(chunk)
        i += step
    
    return chunks


def chunk_file(file_dict: dict, chunk_size: int = 512, overlap: int = 64) -> list[dict]:
    """Chunk a source file and attach metadata to each chunk.
    
    Args:
        file_dict: {"path": str, "content": str, "extension": str, ...}
    """
    chunks = chunk_text(file_dict["content"], chunk_size, overlap)
    
    return [
        {
            "text": chunk,
            "source": file_dict["path"],
            "type": "code" if file_dict["extension"] not in {".md", ".txt"} else "documentation",
            "extension": file_dict["extension"],
            "chunk_index": idx,
        }
        for idx, chunk in enumerate(chunks)
        if chunk.strip()
    ]


def chunk_commit(commit: dict) -> dict:
    """Format a commit as a single chunk with metadata.
    
    Commits are typically short enough to keep whole.
    """
    files_str = ", ".join(commit["files_changed"][:10])
    if len(commit["files_changed"]) > 10:
        files_str += f" (+{len(commit['files_changed']) - 10} more)"
    
    text = (
        f"Commit {commit['sha']} by {commit['author']} on {commit['date']}:\n"
        f"{commit['message']}\n"
        f"Files changed: {files_str}"
    )
    
    return {
        "text": text,
        "source": f"commit:{commit['sha']}",
        "type": "commit",
        "date": commit["date"],
        "author": commit["author"],
        "chunk_index": 0,
    }


def chunk_issue_or_pr(item: dict, item_type: str = "issue") -> list[dict]:
    """Chunk a GitHub issue or pull request.
    
    Args:
        item: {"number": int, "title": str, "body": str, "state": str, ...}
        item_type: "issue" or "pull_request"
    """
    text = (
        f"{item_type.title()} #{item.get('number', '?')}: {item.get('title', '')}\n"
        f"State: {item.get('state', 'unknown')}\n"
        f"Author: {item.get('author', 'unknown')}\n"
        f"\n{item.get('body', '')}"
    )
    
    chunks = chunk_text(text, chunk_size=512, overlap=64)
    
    return [
        {
            "text": chunk,
            "source": f"{item_type}:#{item.get('number', '?')}",
            "type": item_type,
            "chunk_index": idx,
        }
        for idx, chunk in enumerate(chunks)
        if chunk.strip()
    ]
