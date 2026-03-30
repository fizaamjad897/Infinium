"""
GitHub Repository Loader
- Clone/pull repos from GitHub
- Extract commit history with metadata
- Walk source files for indexing
"""
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
    """Clone a GitHub repo or pull latest if it already exists.
    
    If github_token is provided, it will be injected into the URL for private repos.
    """
    if git is None:
        raise ImportError("gitpython is required: pip install gitpython")

    # Inject token for private repo access
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


def extract_commits(repo_path: str, max_commits: int = 500) -> list[dict]:
    """Extract commit history with metadata."""
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


def walk_files(repo_path: str) -> list[dict]:
    """Walk repository and read all supported source files."""
    files = []
    base = Path(repo_path)
    
    for path in base.rglob("*"):
        # Skip ignored directories
        if any(ignored in path.parts for ignored in IGNORE_DIRS):
            continue
        
        if path.suffix.lower() in SUPPORTED_EXTENSIONS and path.is_file():
            try:
                # Skip very large files (> 500KB)
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
    """Check if user has access to a GitHub repository.
    
    Returns: {"has_access": bool, "is_public": bool, "repo_info": dict}
    """
    import httpx
    
    # Extract owner/repo from URL
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
