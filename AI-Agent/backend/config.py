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
    EMBED_MODEL: str = "mxbai-embed-large"
    BRANCH_EMBED_MODEL: str = "mxbai-embed-large"

    # ── Dual-Model Pipeline ──
    # OFF by default for speed — enable per-request with use_dual_model=true
    # Dual-model adds a full extra LLM call (kimi reasoning step) which doubles latency
    USE_DUAL_MODEL: bool = False
    REASONING_TEMPERATURE: float = 0.1
    LLM_TEMPERATURE: float = 0.1         # Low for precise, deterministic answers
    LLM_CONTEXT_WINDOW: int = 4096       # 4096 is plenty for RAG; 8192 doubles wait time

    # ── ChromaDB ──
    CHROMA_DB_PATH: str = str(PROJECT_ROOT / "chroma_db")

    # ── Chunking ──
    CHUNK_SIZE: int = 512         # words
    CHUNK_OVERLAP: int = 128      # words overlap (raised from 64 for better context continuity)
    MAX_COMMITS: int = 200        # reduced: diffs are larger than plain commits

    # Hard cap on chunks generated per single file. Two separate caps so
    # data-heavy files (JSON/YAML) can't blow up the index, but real code
    # files are allowed many chunks (one per function/class).
    MAX_CHUNKS_PER_CODE_FILE: int = 50      # generous — a 50-method class is fine
    MAX_CHUNKS_PER_DATA_FILE: int = 6       # tight — n8n workflow JSONs etc.

    # Use the AST/function-aware chunker for code files. False = fall back to
    # word-count chunking. Leave True for code refactoring quality.
    AST_CHUNKING_ENABLED: bool = True

    # Comma-separated list of file extensions to skip during ingestion entirely.
    # Default skips lockfiles. Add e.g. ".json,.csv" via .env for data-heavy repos.
    SKIP_EXTENSIONS: str = ".lock"

    # For data-heavy formats, use a much larger chunk size — fewer chunks
    # per file because they're rarely worth fine-grained retrieval.
    LARGE_CHUNK_EXTENSIONS: str = ".json,.yaml,.yml,.csv,.txt"
    LARGE_CHUNK_SIZE: int = 2048

    # ── RAG ──
    RAG_TOP_K: int = 15             # Increased from 8 for better architectural context
    RELEVANCE_THRESHOLD: float = 0.40  # Higher threshold filters weak matches

    # ── Embedding throughput ──
    # We batch many texts into a single Ollama /api/embed call (the newer
    # batch endpoint), then run several batches in parallel.
    EMBED_BATCH_SIZE: int = 4          # Reduced for 1GB RAM stability
    EMBED_WORKERS: int = 1             # Reduced for 1GB RAM stability (prevent OOM)
    EMBED_PROGRESS_EVERY: int = 50     # status update every N chunks during embedding
    # Set to true to skip the batch endpoint and go straight to per-text.
    DISABLE_BATCH_EMBED: bool = False

    # ── Retrieval throughput ──
    # For multi-repo queries, we run retrieval tasks in parallel.
    RETRIEVAL_WORKERS: int = 4

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

    # ── GitHub API Token (server-level, for remote URL ingestion rate limits) ──
    # Optional but recommended: generate a classic PAT with no special scopes at
    # https://github.com/settings/tokens — this lifts the rate limit from 60/hr to 5000/hr
    # for all POST /api/ingest/url requests, without requiring users to pass a token.
    GITHUB_API_TOKEN: str = ""

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
