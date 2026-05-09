"""
GitHub Repository Loader — Enhanced Version
- Clone/pull repos from GitHub
- Extract commit history + full diffs
- Extract all branches with HEAD metadata
- Walk source files for indexing
- File blame extraction for key files
"""
from __future__ import annotations
import os
from pathlib import Path
from typing import Optional

try:
    import git
except ImportError:
    git = None


SUPPORTED_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx",
    ".java", ".go", ".rs", ".cpp", ".c", ".h",
    ".md", ".txt", ".yaml", ".yml", ".json",
    ".html", ".css", ".scss",
    ".sql", ".sh", ".dockerfile",
}

IGNORE_DIRS = {
    "node_modules", ".git", "__pycache__", ".next",
    "dist", "build", ".venv", "venv", "env",
    ".mypy_cache", ".pytest_cache", "coverage",
}


def clone_repo(repo_url: str, dest: str, github_token: Optional[str] = None,
               shallow_depth: int = 60) -> str:
    """Clone a GitHub repo (shallow by default) or pull latest if it already exists.

    A shallow clone with ``--depth=shallow_depth`` skips all but the most recent
    N commits, which avoids downloading the entire history when a repo has a
    bloated ``.git`` (e.g. binary model files in old commits). This typically
    cuts clone time from minutes to seconds.

    Set ``shallow_depth`` slightly higher than ``settings.MAX_COMMITS`` so commit
    diff extraction still has commits to walk.
    """
    if git is None:
        raise ImportError("gitpython is required: pip install gitpython")

    if github_token and "github.com" in repo_url:
        repo_url = repo_url.replace(
            "https://github.com",
            f"https://{github_token}@github.com"
        )

    if os.path.exists(os.path.join(dest, ".git")):
        print(f"   📥 Pulling latest changes...")
        repo = git.Repo(dest)
        try:
            repo.remotes.origin.pull()
        except Exception as e:
            print(f"   ⚠️ Pull failed (may be clean): {e}")
    else:
        print(f"   📥 Cloning repository (shallow, depth={shallow_depth})...")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        try:
            repo = git.Repo.clone_from(repo_url, dest, depth=shallow_depth, single_branch=True)
        except Exception as e:
            # Some hosts/refs reject shallow — fall back to full clone
            print(f"   ⚠️ Shallow clone failed ({e}); retrying full clone...")
            import shutil
            if os.path.exists(dest):
                shutil.rmtree(dest, ignore_errors=True)
            repo = git.Repo.clone_from(repo_url, dest)

    return dest


def extract_commits(repo_path: str, max_commits: int = 200) -> list[dict]:
    """Extract basic commit history (message + files list, no diff)."""
    if git is None:
        return []
    try:
        repo = git.Repo(repo_path)
    except Exception:
        return []

    commits = []
    for commit in list(repo.iter_commits())[:max_commits]:
        try:
            commits.append({
                "sha": commit.hexsha[:8],
                "message": commit.message.strip(),
                "author": str(commit.author),
                "date": commit.authored_datetime.isoformat(),
                "files_changed": list(commit.stats.files.keys())[:20],
            })
        except Exception:
            continue

    return commits


def extract_commit_diffs(repo_path: str, max_commits: int = 100) -> list[dict]:
    """Extract commit history WITH actual diff patches.

    Returns richer data than extract_commits — includes the actual changed code
    so the LLM can reason about *what* changed, not just *which files* changed.

    Limits diff to 2000 chars per commit to stay within embedding budget.
    """
    if git is None:
        return []
    try:
        repo = git.Repo(repo_path)
    except Exception:
        return []

    commits = []
    commit_list = list(repo.iter_commits())[:max_commits]

    for commit in commit_list:
        try:
            # Get unified diff for this commit vs its parent
            diff_text = ""
            if commit.parents:
                diffs = commit.parents[0].diff(commit, create_patch=True)
                patches = []
                for d in diffs:
                    try:
                        if d.diff:
                            patch = d.diff.decode("utf-8", errors="ignore")
                            patches.append(f"--- {d.a_path}\n{patch[:500]}")
                    except Exception:
                        continue
                diff_text = "\n".join(patches)
                # Hard cap: 2000 chars of diff per commit
                if len(diff_text) > 2000:
                    diff_text = diff_text[:2000] + "\n... [diff truncated]"

            commits.append({
                "sha": commit.hexsha[:8],
                "message": commit.message.strip(),
                "author": str(commit.author),
                "date": commit.authored_datetime.isoformat(),
                "files_changed": list(commit.stats.files.keys())[:20],
                "diff": diff_text,
            })
        except Exception:
            continue

    return commits


def extract_branches(repo_path: str) -> list[dict]:
    """Extract all branches with their HEAD commit metadata.

    Returns data like:
    {name, last_commit_sha, last_commit_author, last_commit_date,
     last_commit_message, merged, is_default}
    """
    if git is None:
        return []
    try:
        repo = git.Repo(repo_path)
    except Exception:
        return []

    branches = []
    try:
        default_branch = repo.active_branch.name
    except Exception:
        default_branch = "main"

    for branch in repo.branches:
        try:
            commit = branch.commit
            branches.append({
                "name": branch.name,
                "last_commit_sha": commit.hexsha[:8],
                "last_commit_author": str(commit.author),
                "last_commit_date": commit.authored_datetime.isoformat(),
                "last_commit_message": commit.message.strip()[:200],
                "merged": False,  # local branches — can't determine merge status without remote
                "is_default": branch.name == default_branch,
            })
        except Exception:
            continue

    # Also check remote tracking branches
    try:
        for ref in repo.remote().refs:
            branch_name = ref.name.replace("origin/", "")
            if branch_name in {"HEAD"} or any(b["name"] == branch_name for b in branches):
                continue
            try:
                commit = ref.commit
                branches.append({
                    "name": f"remote/{branch_name}",
                    "last_commit_sha": commit.hexsha[:8],
                    "last_commit_author": str(commit.author),
                    "last_commit_date": commit.authored_datetime.isoformat(),
                    "last_commit_message": commit.message.strip()[:200],
                    "merged": False,
                    "is_default": branch_name == default_branch,
                })
            except Exception:
                continue
    except Exception:
        pass  # no remote configured — fine for local repos

    print(f"   🌿 Found {len(branches)} branches")
    return branches


def extract_file_blame(repo_path: str, file_path: str) -> list[dict]:
    """Get line-level blame info for a specific file.

    Returns: list of {line_no, author, date, sha, content}
    Useful for 'who wrote this?' queries.
    """
    if git is None:
        return []
    try:
        repo = git.Repo(repo_path)
        blame = repo.blame("HEAD", file_path)
        lines = []
        for commit, line_contents in blame:
            for line in line_contents:
                lines.append({
                    "sha": commit.hexsha[:8],
                    "author": str(commit.author),
                    "date": commit.authored_datetime.isoformat(),
                    "content": line.decode("utf-8", errors="ignore").rstrip(),
                })
        return lines
    except Exception:
        return []


def walk_files(repo_path: str) -> list[dict]:
    """Walk repository and read all supported source files."""
    from backend.config import settings

    skip_patterns = [p.strip() for p in (settings.SKIP_PATH_PATTERNS or "").split(",") if p.strip()]
    binary_exts = {e.strip().lower() for e in (settings.BINARY_EXTENSIONS or "").split(",") if e.strip()}

    files = []
    skipped_by_pattern = 0
    skipped_binary = 0
    base = Path(repo_path)

    for path in base.rglob("*"):
        if any(ignored in path.parts for ignored in IGNORE_DIRS):
            continue

        ext = path.suffix.lower()
        if ext in binary_exts:
            skipped_binary += 1
            continue

        if ext in SUPPORTED_EXTENSIONS and path.is_file():
            rel = str(path.relative_to(base)).replace("\\", "/")
            if any(pat in rel for pat in skip_patterns):
                skipped_by_pattern += 1
                continue
            try:
                if path.stat().st_size > 500_000:
                    continue
                content = path.read_text(encoding="utf-8", errors="ignore")
                if content.strip():
                    files.append({
                        "path": rel,
                        "content": content,
                        "extension": ext,
                        "size": len(content),
                    })
            except Exception:
                continue

    if skipped_by_pattern:
        print(f"   🚫 Skipped {skipped_by_pattern} files via SKIP_PATH_PATTERNS")
    if skipped_binary:
        print(f"   🚫 Skipped {skipped_binary} binary files (models/media/archives)")
    return files


def list_all_branch_names(repo_path: str) -> list[str]:
    """Names of all branches (local + remote) in a cloned repo."""
    if git is None:
        return []
    try:
        repo = git.Repo(repo_path)
        names = set()
        # Local
        for b in repo.branches:
            names.add(b.name)
        # Remote
        try:
            for ref in repo.remote().refs:
                name = ref.name.replace("origin/", "")
                if name != "HEAD":
                    names.add(name)
        except Exception:
            pass
        return sorted(list(names))
    except Exception:
        return []


def fetch_all_remote_branches(repo_path: str) -> None:
    """Fetch all remote branches into local tracking refs so we can checkout any branch."""
    if git is None:
        return
    try:
        repo = git.Repo(repo_path)
        for remote in repo.remotes:
            try:
                remote.fetch()
            except Exception as e:
                print(f"   ⚠️ Failed to fetch from {remote.name}: {e}")
    except Exception as e:
        print(f"   ⚠️ Could not fetch remotes: {e}")


def checkout_branch(repo_path: str, branch_name: str) -> bool:
    """Checkout ``branch_name`` (creating a local tracking branch from origin/<name>
    if needed). Returns True on success.
    """
    if git is None:
        return False
    try:
        repo = git.Repo(repo_path)
        # Local branch exists?
        if branch_name in [b.name for b in repo.branches]:
            repo.git.checkout(branch_name)
            return True
        # Remote tracking ref?
        remote_ref = f"origin/{branch_name}"
        if remote_ref in [r.name for r in repo.remote().refs]:
            repo.git.checkout("-B", branch_name, remote_ref)
            return True
        # Try as a raw ref (tag, sha, etc.)
        repo.git.checkout(branch_name)
        return True
    except Exception as e:
        print(f"   ⚠️ Checkout {branch_name} failed: {e}")
        return False


def walk_files_at_branch(repo_path: str, branch_name: str) -> list[dict]:
    """Checkout ``branch_name`` and walk every supported file at that branch.

    Each returned file dict gets a ``branch`` field so downstream chunkers can
    tag chunks with branch metadata.
    """
    if not checkout_branch(repo_path, branch_name):
        return []
    files = walk_files(repo_path)
    for f in files:
        f["branch"] = branch_name
    return files


def validate_github_access(repo_url: str, github_token: str) -> dict:
    """Check if user has access to a GitHub repository."""
    import httpx

    parts = repo_url.rstrip("/").rstrip(".git").split("/")
    owner, repo = parts[-2], parts[-1]

    headers = {"Authorization": f"token {github_token}"} if github_token else {}

    try:
        resp = httpx.get(
            f"https://api.github.com/repos/{owner}/{repo}",
            headers=headers,
            timeout=10,
        )

        if resp.status_code == 200:
            data = resp.json()
            return {
                "has_access": True,
                "is_public": not data.get("private", True),
                "repo_info": {
                    "full_name": data["full_name"],
                    "description": data.get("description", ""),
                    "default_branch": data.get("default_branch", "main"),
                    "language": data.get("language", ""),
                    "stars": data.get("stargazers_count", 0),
                },
            }
        elif resp.status_code == 404:
            return {"has_access": False, "is_public": False, "repo_info": {}}
        else:
            return {"has_access": False, "is_public": False, "repo_info": {}}
    except Exception:
        return {"has_access": False, "is_public": False, "repo_info": {}}
