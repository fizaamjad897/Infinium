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


def clone_repo(repo_url: str, dest: str, github_token: Optional[str] = None) -> str:
    """Clone a GitHub repo or pull latest if it already exists."""
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
        print(f"   📥 Cloning repository...")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
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
    files = []
    base = Path(repo_path)

    for path in base.rglob("*"):
        if any(ignored in path.parts for ignored in IGNORE_DIRS):
            continue

        if path.suffix.lower() in SUPPORTED_EXTENSIONS and path.is_file():
            try:
                if path.stat().st_size > 500_000:
                    continue
                content = path.read_text(encoding="utf-8", errors="ignore")
                if content.strip():
                    files.append({
                        "path": str(path.relative_to(base)),
                        "content": content,
                        "extension": path.suffix.lower(),
                        "size": len(content),
                    })
            except Exception:
                continue

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
