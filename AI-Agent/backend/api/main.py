"""
Infinium API — FastAPI Application
Main entry point that wires up all routes, middleware, and configuration.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.config import settings

# ── Create FastAPI App ──
app = FastAPI(
    title="Infinium API",
    description=(
        "Infinium — Autonomous Reasoning Agent for Developer Productivity. "
        "Ask natural language questions about your entire codebase across multiple repositories. "
        "Powered by RAG with hybrid retrieval and local LLMs via Ollama."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── CORS Middleware ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Import and register routes ──
from backend.auth.github_oauth import router as auth_router
from backend.api.routes.ingest import router as ingest_router
from backend.api.routes.query import router as query_router
from backend.api.routes.repos import router as repos_router
from backend.api.routes.remote import router as remote_router
from backend.api.routes.git_history import router as git_router
from backend.api.routes.access import router as access_router
from backend.api.routes.dashboard import router as dashboard_router
from backend.api.routes.ide import router as ide_router
from backend.api.routes.slack import router as slack_router
from backend.api.routes.docs import router as docs_router

app.include_router(auth_router)
app.include_router(ingest_router)
app.include_router(query_router)
app.include_router(repos_router)
app.include_router(remote_router)
app.include_router(git_router)
app.include_router(access_router)
app.include_router(dashboard_router)
app.include_router(ide_router)
app.include_router(slack_router)
app.include_router(docs_router)


# ── Startup Event ──
@app.on_event("startup")
def on_startup():
    from backend.config import ensure_directories, settings
    ensure_directories()

    # Cleanup expired conversations
    try:
        from backend.rag.conversation import cleanup_expired
        cleanup_expired()
    except Exception:
        pass

    # Log model configuration
    print(f"   🤖 LLM Model:       {settings.LLM_MODEL}")
    print(f"   🧠 Reasoning Model: {settings.CODE_REASONING_MODEL}")
    print(f"   📐 Embed Model:     {settings.EMBED_MODEL}")
    print(f"   🔀 Dual-Model:      {'ON' if settings.USE_DUAL_MODEL else 'OFF'}")


# ── Health & Root ──
@app.get("/", tags=["System"])
def root():
    """Root endpoint — system info."""
    return {
        "name": "Infinium",
        "version": "1.0.0",
        "description": "Autonomous Reasoning Agent for Developer Productivity",
        "docs": "/docs",
        "status": "running",
    }


@app.get("/api/health", tags=["System"])
def health():
    """Health check endpoint."""
    import requests
    
    # Check Ollama connectivity
    ollama_status = "unknown"
    try:
        resp = requests.get(f"{settings.OLLAMA_BASE_URL}/api/tags", timeout=3)
        if resp.status_code == 200:
            models = [m["name"] for m in resp.json().get("models", [])]
            ollama_status = f"connected ({len(models)} models: {', '.join(models[:5])})"
        else:
            ollama_status = f"error (status {resp.status_code})"
    except Exception as e:
        ollama_status = f"disconnected ({e})"
    
    # Check ChromaDB
    try:
        from backend.ingestion.embedder import list_collections
        collections = list_collections()
        chroma_status = f"ok ({len(collections)} collections)"
    except Exception as e:
        chroma_status = f"error ({e})"
    
    return {
        "status": "healthy",
        "ollama": ollama_status,
        "chromadb": chroma_status,
        "config": {
            "llm_model": settings.LLM_MODEL,
            "embed_model": settings.EMBED_MODEL,
            "github_oauth_configured": bool(settings.GITHUB_CLIENT_ID),
        },
    }
