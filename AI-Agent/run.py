"""
Infinium Backend Startup Script
Run with: python run.py
"""
import uvicorn


if __name__ == "__main__":
    print("")
    print("=" * 55)
    print("  INFINIUM AI BACKEND")
    print("  Autonomous Reasoning Agent for Developers")
    print("-" * 55)
    print("  API Docs:     http://localhost:8000/docs")
    print("  Health:       http://localhost:8000/api/health")
    print("  GitHub Auth:  http://localhost:8000/api/auth/github")
    print("=" * 55)
    print("")
    
    uvicorn.run(
        "backend.api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )
