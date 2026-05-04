"""
Remote Loader — Fetches public GitHub repos via the GitHub API.

Primary strategy: **tarball download** (one HTTP call, no secondary rate
limits). Falls back to per-file Contents API only when the tarball fails.

Rate-limit notes:
  - Anonymous primary: 60 req/hr
  - Token primary:     5000 req/hr
  - Secondary limit:   triggered by many concurrent requests on the same
    endpoint (e.g. /contents/{path}). Returns 403 — NOT solved by the PAT.
    Tarball avoids this entirely.
"""
from __future__ import annotations
import base64
import io
import tarfile
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


def _sleep_for_rate_limit(resp: requests.Response, attempt: int) -> float:
    """Read GitHub's rate-limit hints and return how long to back off.

    Honors Retry-After (seconds) and X-RateLimit-Reset (epoch) headers.
    Falls back to exponential backoff (2, 4, 8, 16 s) if no header is present.
    """
    retry_after = resp.headers.get("Retry-After")
    if retry_after:
        try:
            return float(retry_after)
        except ValueError:
            pass
    reset = resp.headers.get("X-RateLimit-Reset")
    if reset:
        try:
            now = time.time()
            wait = max(0, float(reset) - now)
            return min(wait, 60)   # cap at 60 s per attempt
        except ValueError:
            pass
    return min(2 ** attempt, 60)   # 2, 4, 8, 16, … capped at 60


def fetch_file_content(
    owner: str,
    repo: str,
    path: str,
    github_token: Optional[str] = None,
    max_attempts: int = 4,
) -> Optional[dict]:
    """Fetch a single file's content via the Contents API.

    Honors GitHub's secondary-rate-limit signals (403 + Retry-After /
    X-RateLimit-Reset) with bounded exponential backoff. Used only as
    a fallback — primary path is the tarball.
    """
    token = _resolve_token(github_token)
    url = f"{GITHUB_API}/repos/{owner}/{repo}/contents/{path}"

    for attempt in range(max_attempts):
        try:
            resp = requests.get(url, headers=_headers(token), timeout=20)
        except Exception as e:
            print(f"   ⚠️ Network error fetching {path}: {e}")
            return None

        if resp.status_code == 404:
            return None
        if resp.status_code in (403, 429):
            wait = _sleep_for_rate_limit(resp, attempt)
            print(f"   ⏳ Rate-limited on {path} — sleeping {wait:.1f}s (attempt {attempt+1}/{max_attempts})")
            time.sleep(wait)
            continue
        if resp.status_code != 200:
            print(f"   ⚠️ {path} returned {resp.status_code}")
            return None

        try:
            data = resp.json()
            encoded = data.get("content", "")
            content = base64.b64decode(encoded).decode("utf-8", errors="ignore")
        except Exception as e:
            print(f"   ⚠️ Decode error on {path}: {e}")
            return None
        if not content.strip():
            return None
        ext = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""
        return {"path": path, "content": content, "extension": ext, "size": len(content)}

    print(f"   ⚠️ Gave up on {path} after {max_attempts} rate-limited attempts")
    return None


def fetch_via_tarball(
    owner: str,
    repo: str,
    ref: str,
    github_token: Optional[str] = None,
) -> list[dict]:
    """Download the repo as a tar.gz at ``ref`` (branch / tag / sha) — one API call.

    Tarball downloads do **not** count against the secondary rate limit and
    are dramatically faster for large repos (one round-trip vs. thousands).
    GitHub serves tarballs from a CDN; expect ~10 MB/s on a decent connection.
    """
    token = _resolve_token(github_token)
    url = f"{GITHUB_API}/repos/{owner}/{repo}/tarball/{ref}"
    print(f"   📦 Downloading tarball: {owner}/{repo}@{ref} …")
    started = time.time()

    # Bounded retry on transient errors / rate-limit on the tarball endpoint itself
    last_exc: Optional[Exception] = None
    for attempt in range(3):
        try:
            resp = requests.get(url, headers=_headers(token), timeout=300, stream=True)
            if resp.status_code in (403, 429):
                wait = _sleep_for_rate_limit(resp, attempt)
                print(f"   ⏳ Tarball rate-limited — sleeping {wait:.1f}s")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            tarball_bytes = resp.content     # whole archive into memory
            break
        except Exception as e:
            last_exc = e
            time.sleep(2 ** attempt)
    else:
        raise RuntimeError(f"Tarball download failed after retries: {last_exc}")

    size_mb = len(tarball_bytes) / 1_000_000
    print(f"   📦 Got {size_mb:.1f} MB in {time.time()-started:.1f}s — extracting…")

    files: list[dict] = []
    skipped_too_big = 0
    skipped_unsupported = 0

    with tarfile.open(fileobj=io.BytesIO(tarball_bytes), mode="r:gz") as tar:
        for member in tar:
            if not member.isfile():
                continue
            # Tarball wraps everything in a top-level "{owner}-{repo}-{sha}/" dir — strip it
            name = member.name
            if "/" in name:
                name = name.split("/", 1)[1]
            else:
                continue
            if not name:
                continue

            ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
            if ext not in SUPPORTED_EXTENSIONS:
                skipped_unsupported += 1
                continue
            if member.size > MAX_FILE_SIZE_BYTES:
                skipped_too_big += 1
                continue

            try:
                fh = tar.extractfile(member)
                if fh is None:
                    continue
                content = fh.read().decode("utf-8", errors="ignore")
            except Exception as e:
                print(f"   ⚠️ Could not read {name}: {e}")
                continue
            if not content.strip():
                continue

            files.append({
                "path": name,
                "content": content,
                "extension": ext,
                "size": len(content),
            })

    print(f"   ✅ Extracted {len(files)} files "
          f"(skipped {skipped_unsupported} unsupported, {skipped_too_big} oversized)")
    return files


def fetch_all_files(
    owner: str,
    repo: str,
    branch: str = "main",
    github_token: Optional[str] = None,
    max_workers: int = 4,
) -> list[dict]:
    """Fetch every supported source file in a repo branch.

    Strategy:
      1. **Tarball** (default) — one HTTP call, no secondary rate limits.
         Handles repos of any size in one shot.
      2. **Per-file Contents API** (fallback) — only used if tarball fails
         (e.g. ref doesn't exist). Has 403/Retry-After backoff.

    Auto-retries main↔master if the requested branch returns 404.
    """
    token = _resolve_token(github_token)

    # Try tarball first
    try:
        return fetch_via_tarball(owner, repo, branch, token)
    except Exception as e:
        msg = str(e)
        # Fall back to alternate default branch on 404
        if "404" in msg:
            fallback = "master" if branch != "master" else "main"
            print(f"   ⚠️ Tarball: branch '{branch}' not found — retrying with '{fallback}' …")
            try:
                return fetch_via_tarball(owner, repo, fallback, token)
            except Exception as e2:
                print(f"   ⚠️ Tarball fallback also failed: {e2}")
        else:
            print(f"   ⚠️ Tarball fetch failed: {e}")

    # ── Per-file fallback (slow path) ──
    print(f"   🌐 Falling back to per-file Contents API (slower) …")
    try:
        tree = fetch_repo_tree(owner, repo, branch, token)
    except Exception as e:
        if "404" in str(e):
            fallback = "master" if branch != "master" else "main"
            tree = fetch_repo_tree(owner, repo, fallback, token)
        else:
            raise

    print(f"   📄 Found {len(tree)} supported files to fetch individually")
    files = []
    # Lower concurrency on the slow path to stay below the secondary limit
    with ThreadPoolExecutor(max_workers=min(max_workers, 2)) as executor:
        futures = {
            executor.submit(fetch_file_content, owner, repo, item["path"], token): item
            for item in tree
        }
        for future in as_completed(futures):
            result = future.result()
            if result:
                files.append(result)
            time.sleep(0.1)
    print(f"   ✅ Fetched {len(files)}/{len(tree)} files via Contents API")
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
