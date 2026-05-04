"""
Ingestion Pipeline Orchestrator — Enhanced Version
- GitHub repos: clone → files + branches + commit diffs → chunk → embed → store
- Local folders: files + optional git history → chunk → embed → store
- Remote URL: GitHub API fetch (no clone) → files + commits + branches → chunk → embed → store
- Tracks enriched status: branches_processed, diffs_processed
"""
from __future__ import annotations
import os
import json
import time
from datetime import datetime
from typing import Optional

from backend.ingestion.github_loader import (
    clone_repo, walk_files,
    extract_commits, extract_commit_diffs, extract_branches,
)
from backend.ingestion.chunker import (
    chunk_file, chunk_commit, chunk_commit_diff, chunk_branch,
)
from backend.ingestion.embedder import get_chroma_collection, embed_and_store, delete_collection
from backend.config import settings


_ingestion_status: dict[str, dict] = {}
STATUS_FILE = os.path.join(settings.DATA_DIR, "ingestion_status.json")


def _post_callback(callback_url: str, payload: dict):
    """Fire-and-forget POST to a user-supplied callback URL when ingestion finishes."""
    if not callback_url:
        return
    try:
        import requests
        requests.post(callback_url, json=payload, timeout=10)
        print(f"   📨 Notified callback: {callback_url}")
    except Exception as e:
        print(f"   ⚠️ Callback POST failed ({callback_url}): {e}")


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
    _load_status()
    return _ingestion_status.get(repo_name, {"status": "unknown"})


def get_all_repos() -> list[dict]:
    _load_status()
    return [{"repo_name": name, **status} for name, status in _ingestion_status.items()]


def _update_status(repo_name: str, status: str, **kwargs):
    _load_status()
    if repo_name not in _ingestion_status:
        _ingestion_status[repo_name] = {}
    # Clear stale error from previous runs when transitioning to a new active status
    active_statuses = {"cloning", "scanning_files", "fetching_repo_info",
                       "fetching_files", "fetching_commits", "fetching_branches",
                       "extracting_branches", "extracting_diffs", "chunking", "embedding"}
    if status in active_statuses:
        _ingestion_status[repo_name].pop("error", None)
    _ingestion_status[repo_name].update({
        "status": status,
        "updated_at": datetime.now().isoformat(),
        **kwargs,
    })
    _save_status()


def _chunk_and_store(files, commits_with_diffs, commits_basic, branches, repo_name, collection):
    """Shared chunk+embed+store logic used by all ingest paths."""
    all_chunks = []

    # File chunks (with contextual prefix)
    for f in files:
        all_chunks.extend(chunk_file(f, settings.CHUNK_SIZE, settings.CHUNK_OVERLAP))

    # Commit diff chunks (rich — includes actual diff patch)
    for c in commits_with_diffs:
        all_chunks.append(chunk_commit_diff(c))

    # Basic commit chunks for any commits not in diffs list
    diff_shas = {c.get("sha") for c in commits_with_diffs}
    for c in commits_basic:
        if c.get("sha") not in diff_shas:
            all_chunks.append(chunk_commit(c))

    # Branch chunks
    for b in branches:
        all_chunks.append(chunk_branch(b))

    total_chunks = len(all_chunks)
    print(f"   📦 Total chunks: {total_chunks} "
          f"(files: {sum(1 for c in all_chunks if c['type'] in ('code', 'documentation'))}, "
          f"diffs: {sum(1 for c in all_chunks if c['type'] == 'commit_diff')}, "
          f"commits: {sum(1 for c in all_chunks if c['type'] == 'commit')}, "
          f"branches: {sum(1 for c in all_chunks if c['type'] == 'branch')})")

    embed_started_at = time.time()

    def _on_progress(done: int, total: int):
        elapsed = time.time() - embed_started_at
        rate = done / elapsed if elapsed > 0 else 0
        remaining = (total - done) / rate if rate > 0 else 0
        _update_status(
            repo_name, "embedding",
            chunks_processed=done,
            chunks_total=total,
            percent_complete=round(100 * done / total, 1) if total else 0,
            embed_rate_per_sec=round(rate, 2),
            eta_seconds=int(remaining),
        )

    stored = embed_and_store(all_chunks, collection, repo_name=repo_name, progress_callback=_on_progress)
    return stored, total_chunks


def ingest_repository(
    repo_url: str,
    repo_name: str,
    github_token: Optional[str] = None,
    ingested_by: str = "",
    callback_url: str = "",
) -> dict:
    """Full ingestion pipeline for a GitHub repository (cloned)."""
    print(f"\n🚀 Starting ingestion for: {repo_name} ({repo_url})")
    start_time = time.time()
    _update_status(repo_name, "cloning", repo_url=repo_url, ingested_by=ingested_by)

    try:
        dest = os.path.join(settings.REPO_CACHE_DIR, repo_name.replace("/", "_"))
        path = clone_repo(repo_url, dest, github_token=github_token)

        _update_status(repo_name, "scanning_files")
        print(f"   🔍 Scanning files...")
        files = walk_files(path)
        print(f"   📄 Found {len(files)} source files")

        _update_status(repo_name, "extracting_branches")
        print(f"   🌿 Extracting branches...")
        branches = extract_branches(path)

        _update_status(repo_name, "extracting_diffs")
        print(f"   📜 Extracting commit diffs (last {settings.MAX_COMMITS} commits)...")
        commits_with_diffs = extract_commit_diffs(path, max_commits=settings.MAX_COMMITS)
        print(f"   📜 Got {len(commits_with_diffs)} commits with diffs")

        _update_status(repo_name, "chunking")
        print(f"   ✂️ Chunking content...")

        _update_status(repo_name, "embedding")
        print(f"   🧮 Generating embeddings and storing...")
        collection = get_chroma_collection(repo_name)
        stored, total = _chunk_and_store(files, commits_with_diffs, [], branches, repo_name, collection)

        elapsed = round(time.time() - start_time, 1)
        stats = {
            "chunks_stored": stored,
            "files_processed": len(files),
            "commits_processed": len(commits_with_diffs),
            "branches_processed": len(branches),
            "total_chunks": total,
            "elapsed_seconds": elapsed,
        }
        _update_status(repo_name, "complete", repo_url=repo_url, **stats)
        print(f"   ✅ Ingestion complete in {elapsed}s!")
        _post_callback(callback_url, {"status": "complete", "repo_name": repo_name, **stats})
        return stats

    except Exception as e:
        _update_status(repo_name, "error", error=str(e))
        print(f"   ❌ Ingestion failed: {e}")
        _post_callback(callback_url, {"status": "error", "repo_name": repo_name, "error": str(e)})
        raise


def ingest_local_folder(
    folder_path: str,
    repo_name: str,
    ingested_by: str = "",
    callback_url: str = "",
) -> dict:
    """Ingest a local folder — walks files + extracts git history if available."""
    print(f"\n🚀 Starting local ingestion for: {repo_name} ({folder_path})")
    start_time = time.time()

    if not os.path.exists(folder_path):
        raise FileNotFoundError(f"Folder not found: {folder_path}")

    _update_status(repo_name, "scanning_files", folder_path=folder_path, ingested_by=ingested_by)
    print(f"   🔍 Scanning files...")
    files = walk_files(folder_path)
    print(f"   📄 Found {len(files)} source files")

    commits_with_diffs = []
    branches = []

    if os.path.exists(os.path.join(folder_path, ".git")):
        _update_status(repo_name, "extracting_branches")
        print(f"   🌿 Extracting branches...")
        branches = extract_branches(folder_path)

        _update_status(repo_name, "extracting_diffs")
        print(f"   📜 Extracting commit diffs...")
        commits_with_diffs = extract_commit_diffs(folder_path, max_commits=settings.MAX_COMMITS)
        print(f"   📜 Got {len(commits_with_diffs)} commits with diffs")

    _update_status(repo_name, "chunking")
    print(f"   ✂️ Chunking content...")

    _update_status(repo_name, "embedding")
    print(f"   🧮 Generating embeddings and storing...")
    collection = get_chroma_collection(repo_name)
    stored, total = _chunk_and_store(files, commits_with_diffs, [], branches, repo_name, collection)

    elapsed = round(time.time() - start_time, 1)
    stats = {
        "chunks_stored": stored,
        "files_processed": len(files),
        "commits_processed": len(commits_with_diffs),
        "branches_processed": len(branches),
        "total_chunks": total,
        "elapsed_seconds": elapsed,
    }
    _update_status(repo_name, "complete", folder_path=folder_path, **stats)
    print(f"   ✅ Ingestion complete in {elapsed}s!")
    _post_callback(callback_url, {"status": "complete", "repo_name": repo_name, **stats})
    return stats


def ingest_remote_url(
    repo_url: str,
    repo_name: str,
    branch: Optional[str] = None,
    github_token: Optional[str] = None,
    ingested_by: str = "",
    callback_url: str = "",
) -> dict:
    """Ingest a public GitHub repo via the GitHub API — no git clone needed.

    Fetches files, commits, and branches using the REST API.
    Works for any public repo without authentication.
    """
    from backend.ingestion.remote_loader import (
        _parse_repo_url, fetch_repo_info, fetch_all_files,
        fetch_repo_commits, fetch_repo_branches,
    )

    print(f"\n🚀 Starting remote URL ingestion for: {repo_name} ({repo_url})")
    start_time = time.time()
    _update_status(repo_name, "fetching_repo_info", repo_url=repo_url, source="remote_url", ingested_by=ingested_by)

    try:
        owner, repo = _parse_repo_url(repo_url)
        info = fetch_repo_info(repo_url, github_token)

        # User-supplied branch is a hint. Use the repo's actual default if not specified.
        # If specified branch doesn't match default, we try user's choice first, then fall back.
        actual_default = info.get("default_branch", "main")
        target_branch = branch if branch else actual_default

        print(f"   ℹ️  {info['full_name']} — branch: {target_branch} (default: {actual_default})")

        _update_status(repo_name, "fetching_files")
        files = fetch_all_files(owner, repo, target_branch, github_token)

        _update_status(repo_name, "fetching_commits")
        print(f"   📜 Fetching commit history...")
        raw_commits = fetch_repo_commits(owner, repo, target_branch, settings.MAX_COMMITS, github_token)
        print(f"   📜 Got {len(raw_commits)} commits")

        _update_status(repo_name, "fetching_branches")
        print(f"   🌿 Fetching branches...")
        raw_branches = fetch_repo_branches(owner, repo, github_token)
        # Mark default branch
        for b in raw_branches:
            b["is_default"] = (b["name"] == target_branch)

        _update_status(repo_name, "chunking")
        print(f"   ✂️ Chunking content...")

        _update_status(repo_name, "embedding")
        print(f"   🧮 Generating embeddings and storing...")
        collection = get_chroma_collection(repo_name)
        # Basic commits (no diffs from REST API list endpoint)
        stored, total = _chunk_and_store(files, [], raw_commits, raw_branches, repo_name, collection)

        elapsed = round(time.time() - start_time, 1)
        stats = {
            "chunks_stored": stored,
            "files_processed": len(files),
            "commits_processed": len(raw_commits),
            "branches_processed": len(raw_branches),
            "total_chunks": total,
            "elapsed_seconds": elapsed,
            "source": "remote_url",
            "ingested_branch": target_branch,
        }
        _update_status(repo_name, "complete", repo_url=repo_url, **stats)
        print(f"   ✅ Remote ingestion complete in {elapsed}s!")
        _post_callback(callback_url, {"status": "complete", "repo_name": repo_name, **stats})
        return stats

    except Exception as e:
        _update_status(repo_name, "error", error=str(e))
        print(f"   ❌ Remote ingestion failed: {e}")
        _post_callback(callback_url, {"status": "error", "repo_name": repo_name, "error": str(e)})
        raise


def remove_repository(repo_name: str):
    delete_collection(repo_name)
    _load_status()
    _ingestion_status.pop(repo_name, None)
    _save_status()
    print(f"   🗑️ Removed repository: {repo_name}")


def reindex_repository(repo_name: str, github_token: Optional[str] = None) -> dict:
    _load_status()
    info = _ingestion_status.get(repo_name, {})
    repo_url = info.get("repo_url", "")
    folder_path = info.get("folder_path", "")
    source = info.get("source", "")

    delete_collection(repo_name)

    if source == "remote_url" or (repo_url and not folder_path):
        return ingest_remote_url(repo_url, repo_name, github_token=github_token)
    elif folder_path:
        return ingest_local_folder(folder_path, repo_name)
    elif repo_url:
        return ingest_repository(repo_url, repo_name, github_token=github_token)
    else:
        raise ValueError(f"No source URL or path found for repo: {repo_name}")
