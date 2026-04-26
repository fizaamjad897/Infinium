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
    git pull origin main 2>/dev/null || true     # skip if not a git repo

    echo "🔨  Building backend image..."
    $COMPOSE build --no-cache backend

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
    echo "⚠️  First deploy? Run model setup:"
    echo "  ./deploy.sh setup-models"
    ;;

  setup-models)
    echo "🔑  Logging into Ollama (cloud models)..."
    docker compose exec ollama ollama login
    echo "📥  Pulling embed model..."
    docker compose exec ollama ollama pull nomic-embed-text
    echo "📥  Pulling cloud LLM (minimax)..."
    docker compose exec ollama ollama run minimax-m2.7:cloud --keepalive 0 || true
    echo "📥  Pulling cloud reasoning model (kimi)..."
    docker compose exec ollama ollama run kimi-k2.5:cloud --keepalive 0 || true
    echo "✅  Models ready!"
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
    echo "Usage: ./deploy.sh [deploy|setup-models|logs|logs-all|stop|restart|status]"
    exit 1
    ;;
esac
