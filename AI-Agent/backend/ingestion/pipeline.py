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

import hashlib
from backend.ingestion.github_loader import (
    clone_repo, walk_files, walk_files_at_branch,
    fetch_all_remote_branches, list_all_branch_names,
    extract_commits, extract_commit_diffs, extract_branches,
)
from backend.ingestion.chunker import (
    chunk_file, chunk_commit, chunk_commit_diff, chunk_branch,
)
from backend.ingestion.embedder import get_chroma_collection, embed_and_store, delete_collection
from backend.config import settings


_ingestion_status: dict[str, dict] = {}
STATUS_FILE = os.path.join(settings.DATA_DIR, "ingestion_status.json")


def _walk_files_multi_branch(repo_path: str, branches: list[str]) -> list[dict]:
    """Walk files at each branch, dedupe by content hash so unchanged files
    across branches are only embedded once.

    Strategy:
      - For every branch, checkout + walk → collect files
      - Hash each file's content (sha1 of bytes)
      - If we've seen the hash before, *append* the branch to that file's
        ``branches`` list instead of duplicating the file
      - Final files list includes ``branches`` (CSV string for ChromaDB
        metadata compatibility) + first branch as ``branch``

    For a 5-branch repo where 90% of files are identical across branches,
    this is ~10× cheaper than naive per-branch indexing.
    """
    seen: dict[str, dict] = {}    # sha1(content) → file_dict
    branch_file_counts: dict[str, int] = {}

    for branch in branches:
        files = walk_files_at_branch(repo_path, branch)
        branch_file_counts[branch] = len(files)
        print(f"   🌿 Branch '{branch}': {len(files)} files")
        for f in files:
            h = hashlib.sha1(f["content"].encode("utf-8", errors="ignore")).hexdigest()
            if h in seen:
                existing = seen[h]
                if branch not in existing["branches_list"]:
                    existing["branches_list"].append(branch)
            else:
                f["branches_list"] = [branch]
                seen[h] = f

    deduped: list[dict] = []
    for h, f in seen.items():
        # Pin "branch" to the first branch we saw it on (for back-compat),
        # plus a CSV string of every branch it appears on.
        f["branch"] = f["branches_list"][0]
        f["branches"] = ",".join(f["branches_list"])
        deduped.append(f)

    print(f"   📁 Multi-branch dedup: {sum(branch_file_counts.values())} total file-reads → "
          f"{len(deduped)} unique files (saved {sum(branch_file_counts.values()) - len(deduped)} duplicate embeds)")
    return deduped


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


def get_ingestion_status(repo_name: str, scope: str = "repo") -> dict:
    _load_status()
    key = f"{repo_name}:{scope}" if scope != "repo" else repo_name
    return _ingestion_status.get(key, {"status": "unknown"})


def get_all_repos() -> list[dict]:
    _load_status()
    return [{"repo_name": name, **status} for name, status in _ingestion_status.items()]


def _update_status(repo_name: str, status: str, status_scope: str = "repo", **kwargs):
    _load_status()
    key = f"{repo_name}:{status_scope}" if status_scope != "repo" else repo_name
    if key not in _ingestion_status:
        _ingestion_status[key] = {}
    
    # Clear stale error from previous runs when transitioning to a new active status
    active_statuses = {"cloning", "scanning_files", "fetching_repo_info",
                       "fetching_files", "fetching_commits", "fetching_branches",
                       "extracting_branches", "extracting_diffs", "chunking", "embedding"}
    if status in active_statuses:
        _ingestion_status[key].pop("error", None)
    
    _ingestion_status[key].update({
        "status": status,
        "updated_at": datetime.now().isoformat(),
        "scope": status_scope,
        **kwargs,
    })
    _save_status()


def _chunk_and_store(files, commits_with_diffs, commits_basic, branches, repo_name, collection, status_scope: str = "repo", embed_model: Optional[str] = None):
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
            status_scope=status_scope,
            chunks_processed=done,
            chunks_total=total,
            percent_complete=round(100 * done / total, 1) if total else 0,
            embed_rate_per_sec=round(rate, 2),
            eta_seconds=int(remaining),
        )

    stored = embed_and_store(all_chunks, collection, repo_name=repo_name, progress_callback=_on_progress, embed_model=embed_model)
    return stored, total_chunks


def ingest_repository(
    repo_url: str,
    repo_name: str,
    github_token: Optional[str] = None,
    ingested_by: str = "",
    callback_url: str = "",
    branch: Optional[str] = None,
    branches: Optional[list[str]] = None,
    embed_model: Optional[str] = None,
    status_scope: str = "repo",
) -> dict:
    """Full ingestion pipeline for a GitHub repository (cloned)."""

    # ========== DEBUG: PRINT ALL PARAMETERS ==========
    print(f"\n{'='*60}")
    print(f"🚀 STARTING INGESTION DEBUG")
    print(f"{'='*60}")
    print(f"   📦 Repo Name: {repo_name}")
    print(f"   🔗 Repo URL: {repo_url}")
    print(f"   🔑 GitHub Token: {'[HIDDEN]' if github_token else 'None'} (length: {len(github_token) if github_token else 0})")
    print(f"   👤 Ingested By: {ingested_by}")
    print(f"   📞 Callback URL: {callback_url or 'None'}")
    print(f"   🌿 Branch (single): {branch or 'None'}")
    print(f"   🌿 Branches (multi): {branches or 'None'}")
    print(f"{'='*60}\n")
    
    start_time = time.time()
    _update_status(repo_name, "cloning", status_scope=status_scope, repo_url=repo_url, ingested_by=ingested_by)

    try:
        dest = os.path.join(settings.REPO_CACHE_DIR, repo_name.replace("/", "_"))
        print(f"   📂 Clone destination: {dest}")
        
        path = clone_repo(repo_url, dest, github_token=github_token)
        print(f"   ✅ Clone successful at: {path}")

        # Auto-discover branches in the background to avoid API timeouts
        if branches is None and branch is None:
            print(f"   🌿 No branch specified — fetching ALL remote branches...")
            fetch_all_remote_branches(path)
            branches = list_all_branch_names(path)
            print(f"   ✅ Found {len(branches)} branches: {branches[:15]}{'...' if len(branches) > 15 else ''}")
        
        # Make sure all remote branches are available locally before checkout
        elif branches or branch:
            print(f"   🌿 Fetching remote branches…")
            fetch_all_remote_branches(path)
            print(f"   ✅ Remote branches fetched")
            
            # List available branches after fetch
            import git
            repo = git.Repo(path)
            available_branches = [b.name for b in repo.branches]
            print(f"   🌿 Available branches: {available_branches[:15]}{'...' if len(available_branches) > 15 else ''}")
            
            if branch and branch not in available_branches:
                print(f"   ⚠️ WARNING: Requested branch '{branch}' NOT found!")
        else:
            print(f"   🌿 Using default branch only")

        _update_status(repo_name, "scanning_files", status_scope=status_scope)
        print(f"   🔍 Scanning files...")

        if branches:
            print(f"   🌿 Multi-branch mode: indexing {len(branches)} branches: {branches}")
            files = _walk_files_multi_branch(path, branches)
        elif branch:
            print(f"   🌿 Single-branch mode: indexing branch '{branch}'")
            files = walk_files_at_branch(path, branch)
        else:
            print(f"   🌿 Default branch mode: indexing default branch")
            files = walk_files(path)
        
        print(f"   📄 {len(files)} source files found")

        _update_status(repo_name, "extracting_branches", status_scope=status_scope)
        print(f"   🌿 Extracting branch metadata...")
        branch_meta = extract_branches(path)
        print(f"   🌿 Found {len(branch_meta)} branches total")
        
        # Print branch names for debugging
        branch_names = [b.get("name", "unknown") for b in branch_meta]
        print(f"   🌿 Branch names: {branch_names}")

        _update_status(repo_name, "extracting_diffs", status_scope=status_scope)
        print(f"   📜 Extracting commit diffs (last {settings.MAX_COMMITS} commits)...")
        commits_with_diffs = extract_commit_diffs(path, max_commits=settings.MAX_COMMITS)
        print(f"   📜 Got {len(commits_with_diffs)} commits with diffs")

        _update_status(repo_name, "chunking", status_scope=status_scope)
        print(f"   ✂️ Chunking content...")

        _update_status(repo_name, "embedding", status_scope=status_scope)
        print(f"   🧮 Generating embeddings and storing...")
        collection = get_chroma_collection(repo_name)
        stored, total = _chunk_and_store(files, commits_with_diffs, [], branch_meta, repo_name, collection, status_scope=status_scope, embed_model=embed_model)

        elapsed = round(time.time() - start_time, 1)
        stats = {
            "chunks_stored": stored,
            "files_processed": len(files),
            "commits_processed": len(commits_with_diffs),
            "branches_processed": len(branch_meta),
            "branches_indexed": branches or ([branch] if branch else []),
            "total_chunks": total,
            "elapsed_seconds": elapsed,
            "ingested_branch": branch if branch else ("multi" if branches else "default"),
        }
        
        print(f"\n{'='*60}")
        print(f"✅ INGESTION COMPLETE")
        print(f"{'='*60}")
        print(f"   📊 Stats: {stats}")
        print(f"{'='*60}\n")
        
        _update_status(repo_name, "complete", status_scope=status_scope, repo_url=repo_url, **stats)
        _post_callback(callback_url, {"status": "complete", "repo_name": repo_name, **stats})
        return stats

    except Exception as e:
        print(f"\n{'='*60}")
        print(f"❌ INGESTION FAILED")
        print(f"{'='*60}")
        print(f"   Error: {e}")
        import traceback
        print(f"   Traceback: {traceback.format_exc()}")
        print(f"{'='*60}\n")
        
        _update_status(repo_name, "error", status_scope=status_scope, error=str(e))
        _post_callback(callback_url, {"status": "error", "repo_name": repo_name, "error": str(e)})
        raise

def ingest_local_folder(
    folder_path: str,
    repo_name: str,
    ingested_by: str = "",
    callback_url: str = "",
    branch: Optional[str] = None,
    branches: Optional[list[str]] = None,
    embed_model: Optional[str] = None,
) -> dict:
    """Ingest a local folder — walks files + extracts git history if available.

    If ``branches=[...]`` is supplied and the folder is a git repo, walks each
    branch (with content-hash dedup) so chunks are tagged per branch.
    """
    print(f"\n🚀 Starting local ingestion for: {repo_name} ({folder_path})")
    start_time = time.time()

    if not os.path.exists(folder_path):
        raise FileNotFoundError(f"Folder not found: {folder_path}")

    is_git = os.path.exists(os.path.join(folder_path, ".git"))

    _update_status(repo_name, "scanning_files", folder_path=folder_path, ingested_by=ingested_by)
    print(f"   🔍 Scanning files...")
    if is_git and branches:
        files = _walk_files_multi_branch(folder_path, branches)
    elif is_git and branch:
        files = walk_files_at_branch(folder_path, branch)
    else:
        files = walk_files(folder_path)
    print(f"   📄 Found {len(files)} source files")

    commits_with_diffs: list[dict] = []
    branch_meta: list[dict] = []

    if is_git:
        _update_status(repo_name, "extracting_branches")
        print(f"   🌿 Extracting branches...")
        branch_meta = extract_branches(folder_path)

        _update_status(repo_name, "extracting_diffs")
        print(f"   📜 Extracting commit diffs...")
        commits_with_diffs = extract_commit_diffs(folder_path, max_commits=settings.MAX_COMMITS)
        print(f"   📜 Got {len(commits_with_diffs)} commits with diffs")

    _update_status(repo_name, "chunking")
    print(f"   ✂️ Chunking content...")

    _update_status(repo_name, "embedding")
    print(f"   🧮 Generating embeddings and storing...")
    collection = get_chroma_collection(repo_name)
    stored, total = _chunk_and_store(files, commits_with_diffs, [], branch_meta, repo_name, collection, embed_model=embed_model)

    elapsed = round(time.time() - start_time, 1)
    stats = {
        "chunks_stored": stored,
        "files_processed": len(files),
        "commits_processed": len(commits_with_diffs),
        "branches_processed": len(branch_meta),
        "branches_indexed": branches or ([branch] if branch else []),
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
    branches: Optional[list[str]] = None,
    status_scope: str = "repo",
    embed_model: Optional[str] = None,
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
    _update_status(repo_name, "fetching_repo_info", status_scope=status_scope, repo_url=repo_url, source="remote_url", ingested_by=ingested_by)

    try:
        owner, repo = _parse_repo_url(repo_url)
        info = fetch_repo_info(repo_url, github_token)

        actual_default = info.get("default_branch", "main")
        # Resolve which branches to fetch:
        #   - branches=[...] → multi-branch via N tarballs
        #   - branch="x"    → single branch
        #   - else          → default branch
        requested_branches: list[str] = list(branches) if branches else [branch or actual_default]
        print(f"   ℹ️  {info['full_name']} — branches to index: {requested_branches} "
              f"(default: {actual_default})")

        # Resolve each requested branch to one that actually exists.
        # If 'master' was requested but the repo's default is 'main', we
        # remap to 'main' so the commits fetch (below) doesn't 404.
        def _resolve_branch(name: str) -> Optional[str]:
            from backend.ingestion.remote_loader import _headers, _resolve_token, GITHUB_API
            token = _resolve_token(github_token)
            try:
                r = httpx.get(
                    f"{GITHUB_API}/repos/{owner}/{repo}/branches/{name}",
                    headers=_headers(token), timeout=8,
                )
                if r.status_code == 200:
                    return name
            except Exception:
                pass
            # Try the alternate default-branch convention
            alt = "main" if name in {"master"} else ("master" if name == "main" else None)
            if alt:
                try:
                    r = httpx.get(
                        f"{GITHUB_API}/repos/{owner}/{repo}/branches/{alt}",
                        headers=_headers(token), timeout=8,
                    )
                    if r.status_code == 200:
                        print(f"   ↪︎ Branch '{name}' not found — using '{alt}' instead")
                        return alt
                except Exception:
                    pass
            # Fall back to the repo's actual default
            if actual_default and actual_default != name:
                print(f"   ↪︎ Branch '{name}' not found — falling back to default '{actual_default}'")
                return actual_default
            return None

        import httpx
        target_branches: list[str] = []
        for b in requested_branches:
            resolved = _resolve_branch(b)
            if resolved and resolved not in target_branches:
                target_branches.append(resolved)
        if not target_branches:
            target_branches = [actual_default]

        _update_status(repo_name, "fetching_files", status_scope=status_scope)
        files: list[dict] = []
        if len(target_branches) == 1:
            # Single-branch: behaviour identical to before
            files = fetch_all_files(owner, repo, target_branches[0], github_token)
            for f in files:
                f["branch"] = target_branches[0]
        else:
            # Multi-branch: one tarball per branch + content-hash dedup
            seen_hash: dict[str, dict] = {}
            for b in target_branches:
                print(f"   🌿 Fetching tarball for branch '{b}'…")
                try:
                    branch_files = fetch_all_files(owner, repo, b, github_token)
                except Exception as e:
                    print(f"   ⚠️ Skipping branch '{b}': {e}")
                    continue
                for f in branch_files:
                    h = hashlib.sha1(f["content"].encode("utf-8", errors="ignore")).hexdigest()
                    if h in seen_hash:
                        existing = seen_hash[h]
                        if b not in existing["branches_list"]:
                            existing["branches_list"].append(b)
                    else:
                        f["branches_list"] = [b]
                        seen_hash[h] = f
            for f in seen_hash.values():
                f["branch"] = f["branches_list"][0]
                f["branches"] = ",".join(f["branches_list"])
                files.append(f)
            print(f"   📁 Multi-branch dedup: {len(files)} unique files across {len(target_branches)} branches")

        _update_status(repo_name, "fetching_commits", status_scope=status_scope)
        print(f"   📜 Fetching commit history (per branch)...")
        raw_commits: list[dict] = []
        for b in target_branches:
            try:
                cs = fetch_repo_commits(owner, repo, b, settings.MAX_COMMITS, github_token)
                for c in cs:
                    c["branch"] = b
                raw_commits.extend(cs)
            except Exception as e:
                print(f"   ⚠️ Could not fetch commits for {b}: {e}")
        print(f"   📜 Got {len(raw_commits)} commits across {len(target_branches)} branches")

        _update_status(repo_name, "fetching_branches", status_scope=status_scope)
        print(f"   🌿 Fetching branches metadata...")
        raw_branches = fetch_repo_branches(owner, repo, github_token)
        for b in raw_branches:
            b["is_default"] = (b["name"] == actual_default)

        _update_status(repo_name, "chunking", status_scope=status_scope)
        print(f"   ✂️ Chunking content...")

        _update_status(repo_name, "embedding", status_scope=status_scope)
        print(f"   🧮 Generating embeddings and storing...")
        collection = get_chroma_collection(repo_name)
        stored, total = _chunk_and_store(files, [], raw_commits, raw_branches, repo_name, collection, status_scope=status_scope, embed_model=embed_model)

        elapsed = round(time.time() - start_time, 1)
        stats = {
            "chunks_stored": stored,
            "files_processed": len(files),
            "commits_processed": len(raw_commits),
            "branches_processed": len(raw_branches),
            "branches_indexed": target_branches,
            "total_chunks": total,
            "elapsed_seconds": elapsed,
            "source": "remote_url",
            "ingested_branch": target_branches[0] if len(target_branches) == 1 else "",
        }
        _update_status(repo_name, "complete", status_scope=status_scope, repo_url=repo_url, **stats)
        print(f"   ✅ Remote ingestion complete in {elapsed}s!")
        _post_callback(callback_url, {"status": "complete", "repo_name": repo_name, **stats})
        return stats

    except Exception as e:
        _update_status(repo_name, "error", status_scope=status_scope, error=str(e))
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
def get_ingestion_status(repo_name: str) -> Optional[dict]:
    """Get the current status of an ingestion task."""
    _load_status()
    return _ingestion_status.get(repo_name)

def list_all_statuses() -> dict:
    """List all ingestion statuses."""
    _load_status()
    return _ingestion_status
