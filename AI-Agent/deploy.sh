#!/bin/bash
# ─────────────────────────────────────────────────────────────
# Infinium — Deploy / Redeploy Script
# Usage:
#   First deploy : ./deploy.sh
#   Redeploy     : ./deploy.sh          (pulls latest, rebuilds, restarts)
#   Logs         : ./deploy.sh logs
#   Stop         : ./deploy.sh stop
# ─────────────────────────────────────────────────────────────
set -e

ACTION=${1:-deploy}
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"

case "$ACTION" in

  deploy)
    echo "🚀  Pulling latest code..."
    git pull 2>/dev/null || true     # skip if not a git repo

    echo "🔨  Building backend image (with cache; use './deploy.sh rebuild' for a clean rebuild)..."
    DOCKER_BUILDKIT=1 $COMPOSE build backend

    echo "▶️   Starting all services..."
    $COMPOSE up -d

    echo "⏳  Waiting for backend health check..."
    for i in $(seq 1 30); do
      STATUS=$(curl -s http://localhost:8000/api/health 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status',''))" 2>/dev/null || true)
      if [ "$STATUS" = "healthy" ]; then
        echo "✅  Backend is healthy!"
        break
      fi
      sleep 3
    done

    echo ""
    echo "═══════════════════════════════════════════════"
    echo "  Infinium is running at: http://$(hostname -I | awk '{print $1}'):8000"
    echo "  Swagger docs:           http://$(hostname -I | awk '{print $1}'):8000/docs"
    echo "═══════════════════════════════════════════════"
    echo ""
    echo "ℹ️  This deploy uses a remote Ollama (your Mac) via OLLAMA_BASE_URL."
    echo "    Models live on the Mac — pull them there, not here:"
    echo "      ollama pull mxbai-embed-large"
    echo "      ollama pull minimax-m2.7:cloud"
    echo "      ollama pull kimi-k2.5:cloud"
    echo "      ollama pull qwen3-coder:480b-cloud"
    ;;

  rebuild)
    echo "🔨  Force-rebuilding backend image (no cache)..."
    git pull 2>/dev/null || true
    DOCKER_BUILDKIT=1 $COMPOSE build --no-cache backend
    $COMPOSE up -d
    ;;

  check-ollama)
    URL=$(grep -E "^OLLAMA_BASE_URL=" .env | cut -d= -f2-)
    if [ -z "$URL" ]; then
      echo "❌ OLLAMA_BASE_URL not set in .env"
      exit 1
    fi
    echo "🔎 Verifying remote Ollama at: $URL"
    docker compose -f docker-compose.yml -f docker-compose.prod.yml exec backend \
      python -c "import os, urllib.request, json; r=urllib.request.urlopen(os.environ['OLLAMA_BASE_URL']+'/api/tags', timeout=10).read(); d=json.loads(r); print('Models reachable:'); [print(' - '+m['name']) for m in d.get('models',[])]"
    ;;

  logs)
    $COMPOSE logs -f --tail=100 backend
    ;;

  logs-all)
    $COMPOSE logs -f --tail=50
    ;;

  stop)
    echo "🛑  Stopping Infinium..."
    $COMPOSE down
    ;;

  restart)
    echo "🔄  Restarting backend only..."
    $COMPOSE restart backend
    ;;

  status)
    $COMPOSE ps
    echo ""
    curl -s http://localhost:8000/api/health | python3 -m json.tool 2>/dev/null || echo "Backend not responding"
    ;;

  *)
    echo "Usage: ./deploy.sh [deploy|rebuild|check-ollama|logs|logs-all|stop|restart|status]"
    exit 1
    ;;
esac
