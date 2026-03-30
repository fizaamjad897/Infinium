"""
User Model & Store
- Lightweight JSON file-based user storage for prototype
- In production, replace with PostgreSQL
"""
import os
import json
from datetime import datetime
from backend.config import settings

USERS_FILE = os.path.join(settings.DATA_DIR, "users.json")


class UserStore:
    """Simple JSON-file-based user store."""
    
    def __init__(self):
        self._users: dict[int, dict] = {}
        self._load()
    
    def _load(self):
        if os.path.exists(USERS_FILE):
            try:
                with open(USERS_FILE, "r") as f:
                    data = json.load(f)
                    # Keys are stored as strings in JSON, convert back to int
                    self._users = {int(k): v for k, v in data.items()}
            except Exception:
                self._users = {}
    
    def _save(self):
        try:
            with open(USERS_FILE, "w") as f:
                json.dump(self._users, f, indent=2, default=str)
        except Exception:
            pass
    
    def upsert_user(
        self,
        github_id: int,
        login: str,
        name: str = "",
        email: str = "",
        avatar_url: str = "",
        access_token: str = "",
    ) -> dict:
        """Create or update a user."""
        now = datetime.now().isoformat()
        
        if github_id in self._users:
            # Update existing
            self._users[github_id].update({
                "login": login,
                "name": name,
                "email": email,
                "avatar_url": avatar_url,
                "access_token": access_token,
                "updated_at": now,
            })
        else:
            # Create new
            self._users[github_id] = {
                "github_id": github_id,
                "login": login,
                "name": name,
                "email": email,
                "avatar_url": avatar_url,
                "access_token": access_token,
                "created_at": now,
                "updated_at": now,
            }
        
        self._save()
        return self._users[github_id]
    
    def get_user(self, github_id: int) -> dict | None:
        """Get a user by GitHub ID."""
        self._load()  # Reload for freshness
        return self._users.get(int(github_id))
    
    def get_user_token(self, github_id: int) -> str | None:
        """Get a user's GitHub access token."""
        user = self.get_user(github_id)
        return user.get("access_token") if user else None
    
    def list_users(self) -> list[dict]:
        """List all users (without tokens)."""
        self._load()
        return [
            {k: v for k, v in user.items() if k != "access_token"}
            for user in self._users.values()
        ]
