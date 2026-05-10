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
    # Dedicated model for /api/code/refactor — Qwen3-Coder is purpose-built
    # for code edits and consistently tops code benchmarks.
    CODE_REFACTOR_MODEL: str = "qwen3-coder:480b-cloud"
    EMBED_MODEL: str = "mxbai-embed-large"
    BRANCH_EMBED_MODEL: str = "mxbai-embed-large"

    # ── Dual-Model Pipeline ──
    # OFF by default for speed — enable per-request with use_dual_model=true
    # Dual-model adds a full extra LLM call (kimi reasoning step) which doubles latency
    USE_DUAL_MODEL: bool = False
    REASONING_TEMPERATURE: float = 0.1
    LLM_TEMPERATURE: float = 0.1         # Low for precise, deterministic answers
    LLM_CONTEXT_WINDOW: int = 4096       # 4096 is plenty for RAG; 8192 doubles wait time
    # ── Dual-model speed knobs ──
    # Cap how many tokens the reasoning model (kimi) emits. Without this,
    # kimi writes 2000+ token analyses that nobody reads — 80% of dual-model
    # latency. 512 tokens (~400 words) is plenty for downstream synthesis.
    REASONING_MAX_TOKENS: int = 512
    # Top-K chunks fed to the reasoning step — usually fewer than the synthesis
    # step needs, because reasoning only needs the highest-relevance code, not
    # the full citation set.
    REASONING_TOP_K: int = 8
    # Hard cap on reasoning text spliced into the synthesis prompt. Keeps the
    # synthesis input bounded even if reasoning ignored REASONING_MAX_TOKENS.
    REASONING_MAX_CHARS: int = 2000

    # ── ChromaDB ──
    CHROMA_DB_PATH: str = str(PROJECT_ROOT / "chroma_db")

    # ── Chunking ──
    CHUNK_SIZE: int = 512         # words
    CHUNK_OVERLAP: int = 128      # words overlap (raised from 64 for better context continuity)
    MAX_COMMITS: int = 50         # diffs are large; 50 is plenty for "what changed?" context

    # Hard cap on chunks generated per single file. Two separate caps so
    # data-heavy files (JSON/YAML) can't blow up the index, but real code
    # files are allowed many chunks (one per function/class).
    MAX_CHUNKS_PER_CODE_FILE: int = 20      # one chunk per func/class up to 20 — covers real code files
    MAX_CHUNKS_PER_DATA_FILE: int = 4       # tight — n8n workflow JSONs etc.

    # Use the AST/function-aware chunker for code files. False = fall back to
    # word-count chunking. Leave True for code refactoring quality.
    AST_CHUNKING_ENABLED: bool = True

    # Comma-separated list of file extensions to skip during ingestion entirely.
    # Default skips lockfiles. Add e.g. ".json,.csv" via .env for data-heavy repos.
    SKIP_EXTENSIONS: str = ".lock"

    # Comma-separated path-substring patterns that cause a file to be skipped.
    # Defaults skip translated docs (FastAPI/Vue/etc), vendored code, and minified
    # bundles — common sources of bloat that hurt ingestion time without
    # adding retrieval value.
    # Substrings (case-insensitive) that cause a file to be skipped at walk
    # time. Aggressive defaults — these are paths whose contents are almost
    # never useful for code retrieval/refactor and slow ingestion massively.
    SKIP_PATH_PATTERNS: str = (
        # translated docs
        "docs/de/,docs/es/,docs/fr/,docs/ja/,docs/ko/,docs/pt/,"
        "docs/ru/,docs/tr/,docs/uk/,docs/zh/,docs/zh-hant/,docs/it/,"
        "docs/pl/,docs/nl/,docs/fa/,docs/he/,docs/hi/,docs/id/,"
        "docs/vi/,docs/th/,docs/bn/,docs/ar/,/i18n/,/translations/,"
        # vendored / minified / fixture noise
        "/vendor/,/third_party/,/.min.,fixtures/,testdata/,/snapshots/,"
        # ML model + dataset directories (case-insensitive match below)
        "_models/,/models/,/model_weights/,/models_weights/,/checkpoints/,"
        "/checkpoint/,/pretrained/,/weights/,/saved_models/,/saved_model/,"
        "/ai_models/,/ml_models/,/ml-models/,/training/,/trained/,"
        "/datasets/,/dataset/,/.cache/huggingface/,/.cache/torch/,"
        # build / generated artifacts
        "/build/,/dist/,/out/,/target/,/.next/,/.nuxt/,/.svelte-kit/,"
        "/coverage/,/.coverage/,/htmlcov/,/__pycache__/,"
        # virtualenvs / package caches
        "/venv/,/.venv/,/env/,/site-packages/,/node_modules/"
    )

    # Binary file extensions to never read as source. Cheap guard against
    # accidentally reading 100MB .h5/.pt files even if they slip past
    # SUPPORTED_EXTENSIONS.
    BINARY_EXTENSIONS: str = (
        ".h5,.pt,.pth,.pkl,.pickle,.onnx,.pb,.ckpt,.safetensors,"
        ".bin,.weights,.tflite,.npy,.npz,.joblib,.model,.pmml,"
        ".mp4,.mp3,.wav,.flac,.mov,.avi,.png,.jpg,.jpeg,.gif,"
        ".webp,.svg,.ico,.pdf,.zip,.tar,.gz,.7z,.exe,.dll,.so,.dylib"
    )

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
    # Tuned for tunneled Ollama: 32 texts × 6 workers = 192 in flight is the
    # sweet spot for mxbai-embed-large on Apple Silicon without saturating the
    # tunnel or queueing inside Ollama (which only serves OLLAMA_NUM_PARALLEL
    # requests concurrently per model).
    EMBED_BATCH_SIZE: int = 32         # texts per Ollama /api/embed call
    EMBED_WORKERS: int = 6             # parallel batches in flight (match OLLAMA_NUM_PARALLEL on Mac)
    EMBED_PROGRESS_EVERY: int = 50     # status update every N chunks during embedding
    # Set to true to skip the batch endpoint and go straight to per-text.
    DISABLE_BATCH_EMBED: bool = False
    # HTTP timeout for any single Ollama call. Bump if remote Ollama is slow
    # under load, or if you're routing through a tunnel.
    EMBED_HTTP_TIMEOUT: int = 300

    # ── Retrieval throughput ──
    # For multi-repo queries, we run retrieval tasks in parallel.
    RETRIEVAL_WORKERS: int = 4

    # ── Feedback memory (in-context reinforcement) ──
    # When ON, every rated answer is embedded into a feedback_memory_<target_type>
    # ChromaDB collection. On the next query/refactor/explain, similar high-rated
    # entries are injected as few-shot examples. No model fine-tuning happens —
    # this is purely prompt-time steering.
    USE_FEEDBACK_MEMORY: bool = True
    # Don't even try to recall until we have at least this many entries — below
    # the threshold, retrieved examples are noise.
    FEEDBACK_MEMORY_MIN_EXAMPLES: int = 5
    # How many positive / negative few-shots to inject per call.
    FEEDBACK_MEMORY_TOP_K_POS: int = 3
    FEEDBACK_MEMORY_TOP_K_NEG: int = 1

    # ── Conversation ──
    MAX_CONVERSATION_TURNS: int = 10
    CONVERSATION_TTL_HOURS: int = 24

    # ── GitHub OAuth ──
    GITHUB_CLIENT_ID: str = ""
    GITHUB_CLIENT_SECRET: str = ""
    GITHUB_REDIRECT_URI: str = "http://localhost:5000/api/auth/github/callback"

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
