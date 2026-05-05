import uvicorn
import os
from backend.config import settings

if __name__ == "__main__":
    print("\n🚀 Starting Infinium AI-Agent (Dev Mode)")
    print("📂 Ignoring 'repo_cache' and 'chroma_db' to prevent reload loops...\n")
    
    uvicorn.run(
        "backend.api.main:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=True,
        reload_excludes=["repo_cache/*", "chroma_db/*", "data/*.json"],
        log_level="info"
    )
