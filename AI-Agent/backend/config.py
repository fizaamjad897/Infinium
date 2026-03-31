"""
Infinium Backend - Centralized Configuration
Uses pydantic-settings plus environment variables from .env
"""
import os
from pathlib import Path
from dotenv import load_dotenv
from pydantic_settings import BaseSettings

# Explicitly load .env from project root BEFORE Settings class is created
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"
load_dotenv(ENV_FILE)


class Settings(BaseSettings):
    # ── Ollama ──
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    LLM_MODEL: str = "minimax-m2.7:cloud"
    CODE_REASONING_MODEL: str = "kimi-k2.5:cloud"
    EMBED_MODEL: str = "nomic-embed-text"

    # ── Dual-Model Pipeline ──
    USE_DUAL_MODEL: bool = True          # Use kimi for reasoning + minimax for synthesis
    REASONING_TEMPERATURE: float = 0.2   # Slightly higher for deeper reasoning
    LLM_TEMPERATURE: float = 0.1         # Low for precise final answers
    LLM_CONTEXT_WINDOW: int = 8192

    # ── ChromaDB ──
    CHROMA_DB_PATH: str = str(PROJECT_ROOT / "chroma_db")

    # ── Chunking ──
    CHUNK_SIZE: int = 512         # words
    CHUNK_OVERLAP: int = 64       # words overlap between chunks
    MAX_COMMITS: int = 500

    # ── RAG ──
    RAG_TOP_K: int = 8
    RELEVANCE_THRESHOLD: float = 0.35

    # ── Conversation ──
    MAX_CONVERSATION_TURNS: int = 10
    CONVERSATION_TTL_HOURS: int = 24

    # ── GitHub OAuth ──
    GITHUB_CLIENT_ID: str = ""
    GITHUB_CLIENT_SECRET: str = ""
    GITHUB_REDIRECT_URI: str = "http://localhost:8000/api/auth/github/callback"

    # ── JWT ──
    JWT_SECRET: str = "infinium-dev-secret-change-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_HOURS: int = 24

    # ── GitHub Webhook ──
    GITHUB_WEBHOOK_SECRET: str = ""

    # ── Paths ──
    REPO_CACHE_DIR: str = str(PROJECT_ROOT / "repo_cache")
    DATA_DIR: str = str(PROJECT_ROOT / "data")

    # ── Server ──
    API_HOST: str = "127.0.0.1"
    API_PORT: int = 8000
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:5173"]

    class Config:
        extra = "ignore"


settings = Settings()


def ensure_directories():
    """Create required directories. Called at app startup, not at import time."""
    os.makedirs(settings.CHROMA_DB_PATH, exist_ok=True)
    os.makedirs(settings.REPO_CACHE_DIR, exist_ok=True)
    os.makedirs(settings.DATA_DIR, exist_ok=True)
