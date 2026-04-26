"""
Remote Loader — Fetches public GitHub repos via the GitHub Contents API.
No git clone required. Works for any public repo instantly.
For private repos or higher rate limits, pass a github_token.

Rate limits:
  - Unauthenticated: 60 req/hour
  - With token: 5000 req/hour
"""
from __future__ import annotations
import base64
import time
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

# Supported file extensions (same as github_loader.py)
SUPPORTED_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx",
    ".java", ".go", ".rs", ".cpp", ".c", ".h",
    ".md", ".txt", ".yaml", ".yml", ".json",
    ".html", ".css", ".scss",
    ".sql", ".sh",
}

GITHUB_API = "https://api.github.com"
MAX_FILE_SIZE_BYTES = 400_000  # 400KB cap per file


def _parse_repo_url(repo_url: str) -> tuple[str, str]:
    """Extract owner and repo name from a GitHub URL."""
    url = repo_url.strip()
    if url.endswith(".git"):
        url = url[:-4]  # proper suffix removal, not rstrip (rstrip strips chars not substring)
    url = url.rstrip("/")
    parts = url.split("github.com/")
    if len(parts) < 2:
        raise ValueError(f"Not a valid GitHub URL: {repo_url}")
    path_parts = parts[1].split("/")
    if len(path_parts) < 2:
        raise ValueError(f"Cannot parse owner/repo from: {repo_url}")
    return path_parts[0], path_parts[1]


def _headers(github_token: Optional[str]) -> dict:
    h = {"Accept": "application/vnd.github.v3+json"}
    if github_token:
        # Fine-grained PATs (github_pat_...) and OAuth tokens:
        # GitHub accepts both 'token' and 'Bearer' for classic PATs/OAuth.
        # Fine-grained PATs require 'Bearer'.
        prefix = "Bearer" if github_token.startswith("github_pat_") else "token"
        h["Authorization"] = f"{prefix} {github_token}"
    return h


def _resolve_token(github_token: Optional[str]) -> Optional[str]:
    """Return caller-supplied token, or fall back to the server-level GITHUB_API_TOKEN."""
    if github_token:
        return github_token
    try:
        from backend.config import settings
        return settings.GITHUB_API_TOKEN or None
    except Exception:
        return None


def fetch_repo_info(repo_url: str, github_token: Optional[str] = None) -> dict:
    """Fetch repo metadata. Falls back to unauthenticated if token is rejected on a public repo."""
    owner, repo = _parse_repo_url(repo_url)
    token = _resolve_token(github_token)

    def _try(tok):
        return requests.get(
            f"{GITHUB_API}/repos/{owner}/{repo}",
            headers=_headers(tok),
            timeout=15,
        )

    resp = _try(token)

    # If token is invalid (401) but repo might be public, retry without token
    if resp.status_code == 401 and token:
        print(f"   ⚠️ Token rejected (401) — retrying as anonymous (public repos only)")
        resp = _try(None)

    resp.raise_for_status()
    data = resp.json()
    return {
        "full_name": data["full_name"],
        "description": data.get("description", ""),
        "default_branch": data.get("default_branch", "main"),
        "language": data.get("language", ""),
        "stars": data.get("stargazers_count", 0),
        "private": data.get("private", False),
    }


def fetch_repo_tree(
    owner: str,
    repo: str,
    branch: str = "main",
    github_token: Optional[str] = None,
) -> list[dict]:
    """Fetch the full file tree for a repo branch via the Git Trees API."""
    token = _resolve_token(github_token)
    resp = requests.get(
        f"{GITHUB_API}/repos/{owner}/{repo}/git/trees/{branch}?recursive=1",
        headers=_headers(token),
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    if data.get("truncated"):
        print(f"   ⚠️ Tree truncated by GitHub API (very large repo) — some files may be missing")

    files = []
    for item in data.get("tree", []):
        if item.get("type") != "blob":
            continue
        path = item.get("path", "")
        ext = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""
        size = item.get("size", 0)
        if ext in SUPPORTED_EXTENSIONS and size < MAX_FILE_SIZE_BYTES:
            files.append({"path": path, "sha": item.get("sha", ""), "size": size})

    return files


def fetch_file_content(
    owner: str,
    repo: str,
    path: str,
    github_token: Optional[str] = None,
) -> Optional[dict]:
    """Fetch a single file's content via the Contents API."""
    token = _resolve_token(github_token)
    try:
        resp = requests.get(
            f"{GITHUB_API}/repos/{owner}/{repo}/contents/{path}",
            headers=_headers(token),
            timeout=20,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        encoded = data.get("content", "")
        content = base64.b64decode(encoded).decode("utf-8", errors="ignore")
        if not content.strip():
            return None
        ext = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""
        return {"path": path, "content": content, "extension": ext, "size": len(content)}
    except Exception as e:
        print(f"   ⚠️ Failed to fetch {path}: {e}")
        return None


def fetch_all_files(
    owner: str,
    repo: str,
    branch: str = "main",
    github_token: Optional[str] = None,
    max_workers: int = 4,
) -> list[dict]:
    """Fetch all supported source files. Auto-retries master<->main on 404."""
    token = _resolve_token(github_token)
    print(f"   🌐 Fetching file tree from {owner}/{repo}@{branch}...")

    try:
        tree = fetch_repo_tree(owner, repo, branch, token)
    except Exception as e:
        if "404" in str(e):
            fallback = "master" if branch != "master" else "main"
            print(f"   ⚠️ Branch '{branch}' not found — retrying with '{fallback}'...")
            tree = fetch_repo_tree(owner, repo, fallback, token)
        else:
            raise

    print(f"   📄 Found {len(tree)} supported files to fetch")
    files = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(fetch_file_content, owner, repo, item["path"], token): item
            for item in tree
        }
        for future in as_completed(futures):
            result = future.result()
            if result:
                files.append(result)
            time.sleep(0.05)
    print(f"   ✅ Successfully fetched {len(files)}/{len(tree)} files")
    return files


def fetch_repo_commits(
    owner: str,
    repo: str,
    branch: str = "main",
    max_commits: int = 100,
    github_token: Optional[str] = None,
) -> list[dict]:
    """Fetch commit history via the Commits REST API."""
    token = _resolve_token(github_token)
    commits = []
    page = 1
    per_page = 30

    while len(commits) < max_commits:
        try:
            resp = requests.get(
                f"{GITHUB_API}/repos/{owner}/{repo}/commits",
                headers=_headers(token),
                params={"sha": branch, "per_page": per_page, "page": page},
                timeout=20,
            )
            if resp.status_code == 409:  # Empty repo
                break
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            for item in batch:
                c = item.get("commit", {})
                commits.append({
                    "sha": item.get("sha", "")[:8],
                    "message": c.get("message", "").strip(),
                    "author": c.get("author", {}).get("name", "unknown"),
                    "date": c.get("author", {}).get("date", ""),
                    "files_changed": [],
                })
            page += 1
            if len(batch) < per_page:
                break
        except Exception as e:
            print(f"   ⚠️ Error fetching commits page {page}: {e}")
            break

    return commits[:max_commits]


def fetch_repo_branches(
    owner: str,
    repo: str,
    github_token: Optional[str] = None,
) -> list[dict]:
    """Fetch all branches via the Branches REST API."""
    token = _resolve_token(github_token)
    try:
        resp = requests.get(
            f"{GITHUB_API}/repos/{owner}/{repo}/branches",
            headers=_headers(token),
            params={"per_page": 100},
            timeout=15,
        )
        resp.raise_for_status()
        branches = []
        for item in resp.json():
            commit = item.get("commit", {})
            branches.append({
                "name": item.get("name", "unknown"),
                "last_commit_sha": commit.get("sha", "")[:8],
                "last_commit_author": "",
                "last_commit_date": "",
                "last_commit_message": "",
                "merged": False,
                "is_default": False,
            })
        return branches
    except Exception as e:
        print(f"   ⚠️ Failed to fetch branches: {e}")
        return []
