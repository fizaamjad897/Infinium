import uvicorn
import os
from backend.config import settings

if __name__ == "__main__":
    print("\n🚀 Starting Infinium AI-Agent (Dev Mode)")
    print("📂 Watching only 'backend/' to prevent reload loops during ingestion...\n")

    # IMPORTANT: whitelist via reload_dirs instead of blacklisting via reload_excludes.
    # Glob excludes like "repo_cache/*" only match depth-1 — nested files
    # (repo_cache/<repo>/<sub>/file.py) slip through and trigger reload during
    # ingestion, killing the in-flight job mid-clone or mid-embed.
    uvicorn.run(
        "backend.api.main:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=True,
        reload_dirs=["backend"],
        log_level="info"
    )
