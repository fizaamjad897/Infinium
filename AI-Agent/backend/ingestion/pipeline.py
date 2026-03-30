"""
Ingestion Pipeline Orchestrator
- Ties together: clone → walk → chunk → embed → store
- Supports both GitHub URLs and local folders
- Tracks ingestion status
"""
import os
import json
import time
from datetime import datetime
from typing import Optional

from backend.ingestion.github_loader import clone_repo, walk_files, extract_commits
from backend.ingestion.chunker import chunk_file, chunk_commit
from backend.ingestion.embedder import get_chroma_collection, embed_and_store, delete_collection
from backend.config import settings


# Simple in-memory + file-based status tracking
_ingestion_status: dict[str, dict] = {}

STATUS_FILE = os.path.join(settings.DATA_DIR, "ingestion_status.json")


def _load_status():
    global _ingestion_status
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE, "r") as f:
                _ingestion_status = json.load(f)
        except Exception:
            _ingestion_status = {}


def _save_status():
    try:
        with open(STATUS_FILE, "w") as f:
            json.dump(_ingestion_status, f, indent=2, default=str)
    except Exception:
        pass


def get_ingestion_status(repo_name: str) -> dict:
    """Get the current ingestion status for a repository."""
    _load_status()
    return _ingestion_status.get(repo_name, {"status": "unknown"})


def get_all_repos() -> list[dict]:
    """Get status of all ingested repositories."""
    _load_status()
    return [
        {"repo_name": name, **status}
        for name, status in _ingestion_status.items()
    ]


def _update_status(repo_name: str, status: str, **kwargs):
    """Update ingestion status."""
    _load_status()
    if repo_name not in _ingestion_status:
        _ingestion_status[repo_name] = {}
    _ingestion_status[repo_name].update({
        "status": status,
        "updated_at": datetime.now().isoformat(),
        **kwargs,
    })
    _save_status()


def ingest_repository(
    repo_url: str,
    repo_name: str,
    github_token: Optional[str] = None,
) -> dict:
    """Full ingestion pipeline for a GitHub repository.
    
    1. Clone/pull the repo
    2. Walk source files
    3. Extract commit history
    4. Chunk everything
    5. Generate embeddings and store in ChromaDB
    
    Returns: dict with ingestion stats
    """
    print(f"\n🚀 Starting ingestion for: {repo_name} ({repo_url})")
    start_time = time.time()
    
    _update_status(repo_name, "cloning", repo_url=repo_url)
    
    try:
        # 1. Clone/pull
        dest = os.path.join(settings.REPO_CACHE_DIR, repo_name.replace("/", "_"))
        path = clone_repo(repo_url, dest, github_token=github_token)
        
        # 2. Walk files
        _update_status(repo_name, "scanning_files")
        print(f"   🔍 Scanning files...")
        files = walk_files(path)
        print(f"   📄 Found {len(files)} source files")
        
        # 3. Extract commits
        _update_status(repo_name, "extracting_commits")
        print(f"   📜 Extracting commit history...")
        commits = extract_commits(path, max_commits=settings.MAX_COMMITS)
        print(f"   📜 Found {len(commits)} commits")
        
        # 4. Chunk everything
        _update_status(repo_name, "chunking")
        print(f"   ✂️ Chunking content...")
        all_chunks = []
        
        for f in files:
            file_chunks = chunk_file(f, settings.CHUNK_SIZE, settings.CHUNK_OVERLAP)
            all_chunks.extend(file_chunks)
        
        for c in commits:
            all_chunks.append(chunk_commit(c))
        
        print(f"   📦 Total chunks: {len(all_chunks)}")
        
        # 5. Embed and store
        _update_status(repo_name, "embedding")
        print(f"   🧮 Generating embeddings and storing...")
        collection = get_chroma_collection(repo_name)
        stored = embed_and_store(all_chunks, collection, repo_name=repo_name)
        
        elapsed = round(time.time() - start_time, 1)
        
        stats = {
            "chunks_stored": stored,
            "files_processed": len(files),
            "commits_processed": len(commits),
            "total_chunks": len(all_chunks),
            "elapsed_seconds": elapsed,
        }
        
        _update_status(
            repo_name, "complete",
            repo_url=repo_url,
            **stats,
        )
        
        print(f"   ✅ Ingestion complete in {elapsed}s!")
        print(f"      Files: {len(files)}, Commits: {len(commits)}, Chunks: {len(all_chunks)}")
        
        return stats
        
    except Exception as e:
        _update_status(repo_name, "error", error=str(e))
        print(f"   ❌ Ingestion failed: {e}")
        raise


def ingest_local_folder(
    folder_path: str,
    repo_name: str,
) -> dict:
    """Ingest a local folder (no git clone, just file walk + optional commit history).
    
    This is useful for testing or for repos already on disk.
    """
    print(f"\n🚀 Starting local ingestion for: {repo_name} ({folder_path})")
    start_time = time.time()
    
    if not os.path.exists(folder_path):
        raise FileNotFoundError(f"Folder not found: {folder_path}")
    
    _update_status(repo_name, "scanning_files", folder_path=folder_path)
    
    # Walk files
    print(f"   🔍 Scanning files...")
    files = walk_files(folder_path)
    print(f"   📄 Found {len(files)} source files")
    
    # Try to extract commits if it's a git repo
    commits = []
    if os.path.exists(os.path.join(folder_path, ".git")):
        print(f"   📜 Extracting commit history...")
        commits = extract_commits(folder_path, max_commits=settings.MAX_COMMITS)
        print(f"   📜 Found {len(commits)} commits")
    
    # Chunk
    _update_status(repo_name, "chunking")
    print(f"   ✂️ Chunking content...")
    all_chunks = []
    
    for f in files:
        file_chunks = chunk_file(f, settings.CHUNK_SIZE, settings.CHUNK_OVERLAP)
        all_chunks.extend(file_chunks)
    
    for c in commits:
        all_chunks.append(chunk_commit(c))
    
    print(f"   📦 Total chunks: {len(all_chunks)}")
    
    # Embed and store
    _update_status(repo_name, "embedding")
    print(f"   🧮 Generating embeddings and storing...")
    collection = get_chroma_collection(repo_name)
    stored = embed_and_store(all_chunks, collection, repo_name=repo_name)
    
    elapsed = round(time.time() - start_time, 1)
    
    stats = {
        "chunks_stored": stored,
        "files_processed": len(files),
        "commits_processed": len(commits),
        "total_chunks": len(all_chunks),
        "elapsed_seconds": elapsed,
    }
    
    _update_status(
        repo_name, "complete",
        folder_path=folder_path,
        **stats,
    )
    
    print(f"   ✅ Ingestion complete in {elapsed}s!")
    return stats


def remove_repository(repo_name: str):
    """Remove a repository's data from ChromaDB and status."""
    delete_collection(repo_name)
    _load_status()
    _ingestion_status.pop(repo_name, None)
    _save_status()
    print(f"   🗑️ Removed repository: {repo_name}")


def reindex_repository(repo_name: str, github_token: Optional[str] = None) -> dict:
    """Re-index a repository (delete + ingest again)."""
    _load_status()
    info = _ingestion_status.get(repo_name, {})
    
    repo_url = info.get("repo_url", "")
    folder_path = info.get("folder_path", "")
    
    # Delete old data
    delete_collection(repo_name)
    
    # Re-ingest
    if repo_url:
        return ingest_repository(repo_url, repo_name, github_token=github_token)
    elif folder_path:
        return ingest_local_folder(folder_path, repo_name)
    else:
        raise ValueError(f"No source URL or path found for repo: {repo_name}")


if __name__ == "__main__":
    # Quick test with a local folder
    import sys
    if len(sys.argv) > 1:
        folder = sys.argv[1]
        name = sys.argv[2] if len(sys.argv) > 2 else os.path.basename(folder)
        ingest_local_folder(folder, name)
    else:
        print("Usage: python -m backend.ingestion.pipeline <folder_path> [repo_name]")
