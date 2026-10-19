# Infinium AI Agent — AWS Deployment Guide

Detailed, step-by-step guide for deploying the Infinium AI agent (FastAPI +
ChromaDB + Ollama) to AWS. Targets a single-EC2 reference deployment that
satisfies the SRS non-functional requirements (HTTPS, ≥ 80% uptime, retry
logic, log retention, modular updates).

## TL;DR — readiness verdict

| Capability | Status | Notes |
|---|---|---|
| Containerised (Docker) | ✅ Ready | `Dockerfile`, `docker-compose.yml`, `docker-compose.prod.yml` all present |
| Health check | ✅ Ready | `GET /api/health` |
| Auto-restart | ✅ Ready | `restart: always` in prod compose |
| Persistent volumes | ✅ Ready | `chroma_data`, `repo_cache`, `app_data`, `ollama_data` |
| Background jobs | ✅ Ready | FastAPI BackgroundTasks for ingestion |
| OAuth + JWT | ✅ Ready | GitHub OAuth + bearer JWTs |
| Webhook signature verification | ✅ Ready | HMAC for GitHub + Slack |
| Auto-deploy script | ✅ Ready | `deploy.sh` with build/redeploy/logs/stop |
| HTTPS / TLS | ⚠️ Manual | Provision an ALB or nginx + ACM cert (instructions below) |
| Secrets management | ⚠️ Manual | Env-vars by default. Integrate AWS SSM/Secrets Manager (instructions below) |
| Multi-worker uvicorn | ⚠️ Manual | Single worker by default; instructions to scale below |
| RDS / managed Postgres | ⚪ Optional | App uses JSON files for prototype scale; RDS only needed at >50 active users |

**Bottom line:** Yes — deployable on AWS today. Plan a 30-60 min deploy
following this guide.

---

## 0. Architecture on AWS

```
                    ┌──────────────────┐
   Browser /        │  Route 53 DNS    │
   Slack / Node ────│  api.infinium.io │
   backend          └────────┬─────────┘
                             │
                    ┌────────▼─────────┐    HTTPS (ACM cert)
                    │   ALB / nginx    │
                    └────────┬─────────┘
                             │
                    ┌────────▼─────────┐
                    │   EC2 instance   │
                    │  (t3.large+)     │
                    │                  │
                    │  Docker compose: │
                    │  ┌────────────┐  │
                    │  │  backend   │  │ ← FastAPI :8000
                    │  ├────────────┤  │
                    │  │   ollama   │  │ ← LLM inference :11434
                    │  ├────────────┤  │
                    │  │  postgres  │  │ ← (optional)
                    │  └────────────┘  │
                    │   EBS volume     │
                    │   (chroma_db,    │
                    │    repo_cache)   │
                    └──────────────────┘
```

**Why a single EC2 (not ECS / Fargate / Lambda):**
- Ollama needs persistent local models (~5 GB) and a long-lived process — Lambda's 15 min timeout / cold-start kills it.
- ChromaDB is local-disk; needs persistent volume.
- ECS works but doubles cost and complexity for a prototype.

For scale beyond 50 users, you'd split: ALB → ECS Fargate (backend) → EFS
(ChromaDB) → managed Ollama on a GPU instance.

---

## 1. Provision the EC2 instance

### Pick the right size

| Workload | Instance type | Reason |
|---|---|---|
| Demo / FYP defense (≤ 5 users) | `t3.large` (2 vCPU, 8 GB) | Ollama embed model fits |
| Small team (10-30 users) | `t3.xlarge` (4 vCPU, 16 GB) | Concurrent embedding + inference |
| Heavy use / cloud LLMs | `t3.medium` (2 vCPU, 4 GB) is fine | Cloud LLM offloads inference |

> **Memory note:** Cloud LLMs (`minimax-m2.7:cloud`, `kimi-k2.5:cloud`) run on
> Ollama's servers, so the EC2 only needs to host the `nomic-embed-text`
> embedder (~1 GB) + ChromaDB. **`t3.medium` is plenty if you only use cloud
> models.** Use `t3.large` if you plan to switch to local-only models.

### Step-by-step

```bash
# 1. AWS Console → EC2 → Launch instance
#    AMI:       Ubuntu Server 24.04 LTS
#    Type:      t3.medium (or larger)
#    Storage:   30 GB gp3 EBS (chroma_db + repo_cache will grow)
#    Key pair:  Create new (download the .pem)
#    Network:   Default VPC, public subnet, auto-assign public IP

# 2. Security group inbound rules:
#    Port 22 (SSH)   ← your IP only
#    Port 80 (HTTP)  ← 0.0.0.0/0   (for ACM/Let's Encrypt cert renewal)
#    Port 443 (HTTPS) ← 0.0.0.0/0
#    Port 8000       ← do NOT expose; behind nginx
#    Port 11434      ← do NOT expose; internal Ollama

# 3. Launch + SSH in:
chmod 400 infinium.pem
ssh -i infinium.pem ubuntu@<PUBLIC_IP>
```

### Allocate an Elastic IP

```bash
# AWS Console → EC2 → Elastic IPs → Allocate → Associate to your instance.
# Without this, the public IP changes on stop/start.
```

---

## 2. Install Docker + dependencies on the EC2

```bash
# On the EC2 instance:
sudo apt update && sudo apt upgrade -y
sudo apt install -y git curl python3-pip nginx certbot python3-certbot-nginx

# Docker (official method — Ubuntu's docker.io is too old)
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
newgrp docker      # apply group without re-login

# Docker compose v2 ships as a plugin
docker compose version    # verify: should print "Docker Compose version v2.x"
```

---

## 3. Clone + configure the project

```bash
git clone https://github.com/fizaamjad897/Infinium.git
cd Infinium/AI-Agent

# Create production .env from the template
cp .env.example .env
nano .env
```

Fill in **every** variable below:

```dotenv
# ── Ollama ──
OLLAMA_BASE_URL=http://ollama:11434           # ← in-compose hostname; do NOT change
LLM_MODEL=minimax-m2.7:cloud
CODE_REASONING_MODEL=kimi-k2.5:cloud
EMBED_MODEL=nomic-embed-text
USE_DUAL_MODEL=false

# ── GitHub OAuth ──
# Create at github.com/settings/developers → New OAuth App
# Authorization callback URL:  https://api.infinium.io/api/auth/github/callback
GITHUB_CLIENT_ID=Iv1.xxxxxxxxxxxxxxxx
GITHUB_CLIENT_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
=https://api.infinium.io/api/auth/github/callback

# ── JWT — MUST be a strong random secret in production ──
JWT_SECRET=<openssl rand -hex 64>
JWT_EXPIRE_HOURS=24

# ── GitHub webhook signing ──
GITHUB_WEBHOOK_SECRET=<openssl rand -hex 32>

# ── GitHub server-level token (lifts rate limit 60/hr → 5000/hr) ──
GITHUB_API_TOKEN=REPLACE_WITH_YOUR_TOKEN

# ── Slack (optional) ──
SLACK_SIGNING_SECRET=<from api.slack.com/apps>
SLACK_BOT_TOKEN=xoxb-xxxxxxxx

# ── Postgres (already declared in compose; only needed if you migrate users.json off-disk) ──
POSTGRES_PASSWORD=<openssl rand -hex 16>
```

Generate strong secrets:

```bash
openssl rand -hex 64    # for JWT_SECRET
openssl rand -hex 32    # for GITHUB_WEBHOOK_SECRET
openssl rand -hex 16    # for POSTGRES_PASSWORD
```

> **Lock the `.env` file:** `chmod 600 .env`. Never commit it
> (`AI-Agent/.gitignore` already excludes it).

---

## 4. First-deploy run

```bash
cd ~/Infinium/AI-Agent
chmod +x deploy.sh
./deploy.sh
```

This script (already in the repo):
1. `git pull origin main` (if applicable).
2. Builds the backend Docker image (`--no-cache`).
3. `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d`.
4. Polls `/api/health` for ≤ 90 s and prints the public address.

**Set up Ollama models (first deploy only):**

```bash
./deploy.sh setup-models
# This runs:
#   ollama login                       (you'll paste your ollama.com token)
#   ollama pull nomic-embed-text
#   ollama run minimax-m2.7:cloud
#   ollama run kimi-k2.5:cloud
```

Verify:
```bash
curl http://localhost:8000/api/health
# → {"status":"healthy","ollama":"connected (3 models: ...)","chromadb":"ok (0 collections)"}
```

---

## 5. HTTPS / TLS termination (nginx + Let's Encrypt)

The container exposes port 8000 over HTTP only. Production traffic must be HTTPS.

### 5a. Point your domain at the EC2

In your DNS provider (Route 53 or otherwise), add an A record:
```
api.infinium.io   →   <ELASTIC_IP>
```

Wait until `dig api.infinium.io` returns the IP before continuing.

### 5b. Configure nginx as a reverse proxy

```bash
sudo tee /etc/nginx/sites-available/infinium <<'EOF'
server {
    listen 80;
    server_name api.infinium.io;

    # Increase request size for large diffs / file uploads
    client_max_body_size 50M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # FastAPI streaming/long-lived requests (LLM responses can be 30s+)
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }
}
EOF

sudo ln -sf /etc/nginx/sites-available/infinium /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

You should now be able to hit `http://api.infinium.io/api/health` over HTTP.

### 5c. Get a free TLS cert

```bash
sudo certbot --nginx -d api.infinium.io
# Pick redirect option (auto-rewrites HTTP → HTTPS)
```

certbot patches the nginx config with TLS. Verify:
```bash
curl -I https://api.infinium.io/api/health
# → HTTP/2 200
```

certbot installs a cron job to auto-renew the cert every 60-90 days.

### 5d. Expose **only** 443 to the internet

Once HTTPS works, edit the EC2 security group: keep port 80 (cert renewal),
443 (real traffic), 22 (your IP only). Block direct port 8000 to the world —
already blocked since the container only binds to 127.0.0.1 if you use the
prod compose (verify: it currently binds `8000:8000`).

To restrict properly, edit `docker-compose.prod.yml` and add:
```yaml
services:
  backend:
    ports: !reset
      - "127.0.0.1:8000:8000"   # bind to localhost only
```

---

## 6. Updating the GitHub OAuth callback

Now that you have HTTPS:

1. github.com/settings/developers → your OAuth app
2. Authorization callback URL: `https://api.infinium.io/api/auth/github/callback`
3. Update `.env` to match: `GITHUB_REDIRECT_URI=https://api.infinium.io/api/auth/github/callback`
4. Restart: `./deploy.sh restart`

---

## 7. Production hardening checklist

| Task | Command / location |
|---|---|
| Strong JWT secret | `openssl rand -hex 64` → `.env JWT_SECRET` |
| Strong webhook secret | `openssl rand -hex 32` → `.env GITHUB_WEBHOOK_SECRET` |
| Strong Slack signing secret | from api.slack.com/apps → `.env SLACK_SIGNING_SECRET` |
| `.env` permissions | `chmod 600 .env` |
| CORS — restrict to your dashboard origin | edit `backend/config.py` `CORS_ORIGINS` to `["https://app.infinium.io"]` |
| Disable swagger in production | optional: remove `docs_url=` in `backend/api/main.py` if you don't want public API docs |
| EBS snapshot schedule | AWS Console → EC2 → Volumes → Snapshot lifecycle policy → daily |
| Auto-update OS packages | `sudo apt install unattended-upgrades` |
| Multi-worker uvicorn | edit `Dockerfile` CMD: `uvicorn backend.api.main:app --host 0.0.0.0 --port 8000 --workers 4` |
| Long-poll / SSE friendly nginx | `proxy_read_timeout 300s; proxy_buffering off;` (already in §5b) |
| ALB idle timeout (if using ALB) | Console → ALB → Attributes → set **Idle timeout = 600 s** so SSE streams aren't cut off |
| Ingestion-progress tunables | `.env`: `EMBED_WORKERS=4` (raise on bigger instances), `EMBED_PROGRESS_EVERY=25` |

---

## 8. AWS Secrets Manager integration (optional)

For real production, move secrets out of `.env` and into Secrets Manager:

```bash
aws secretsmanager create-secret \
  --name infinium/prod \
  --secret-string '{"JWT_SECRET":"...","GITHUB_CLIENT_SECRET":"...","SLACK_BOT_TOKEN":"..."}'
```

Add an IAM role to the EC2 with `secretsmanager:GetSecretValue` on
`infinium/prod`, then in `deploy.sh` before `docker compose up`:

```bash
export $(aws secretsmanager get-secret-value --secret-id infinium/prod \
         --query SecretString --output text \
         | jq -r 'to_entries | map("\(.key)=\(.value)") | .[]')
```

---

## 9. Observability

The current build logs to stdout (captured by `docker logs` and rotated by
the `json-file` driver — 50 MB × 5 files in prod compose).

Forward logs to CloudWatch:

```bash
sudo apt install -y awscli
sudo curl -s https://s3.amazonaws.com/amazoncloudwatch-agent/ubuntu/amd64/latest/amazon-cloudwatch-agent.deb -o /tmp/cw.deb
sudo dpkg -i /tmp/cw.deb
# Configure /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json
# to ship /var/lib/docker/containers/*/*.log to a log group `/infinium/prod`
sudo systemctl enable amazon-cloudwatch-agent && sudo systemctl start amazon-cloudwatch-agent
```

Then create alarms (Console → CloudWatch → Alarms):
- `CPUUtilization > 80%` for 5 min → email
- Custom metric on `/api/health` failing → page

---

## 10. Disaster recovery

| Asset | Where | Backup strategy |
|---|---|---|
| ChromaDB collections | EBS volume `/var/lib/docker/volumes/aiagent_chroma_data` | Daily EBS snapshot via lifecycle policy |
| User OAuth tokens (`data/users.json`) | EBS volume `/var/lib/docker/volumes/aiagent_app_data` | Daily snapshot (same) |
| Query history (`data/query_history.json`) | Same | Same |
| Cloned repos (`repo_cache`) | EBS volume `/var/lib/docker/volumes/aiagent_repo_cache` | Skip — re-clone on demand |
| Container images | ECR or Docker Hub | Push on every `./deploy.sh` |

Recovery test:
```bash
docker compose down
docker volume rm aiagent_chroma_data
# Restore from snapshot via AWS Console
docker compose up -d
curl http://localhost:8000/api/repos/    # should list previous repos
```

---

## 11. Updating in production

```bash
ssh ubuntu@api.infinium.io
cd ~/Infinium/AI-Agent
./deploy.sh                # pulls main, rebuilds, restarts, health-checks
./deploy.sh logs           # tail backend logs
./deploy.sh status         # ps + health
./deploy.sh restart        # restart backend only
./deploy.sh stop           # full stop
```

Zero-downtime is **not** guaranteed by the current compose setup. For zero
downtime, switch to a blue/green ECS or two-instance ALB pair.

---

## 12. Cost estimate (us-east-1, on-demand pricing, May 2026)

| Resource | Monthly cost |
|---|---|
| EC2 `t3.medium` (2 vCPU, 4 GB) | ~$30 |
| EC2 `t3.large` (recommended) | ~$60 |
| 30 GB gp3 EBS | ~$3 |
| Elastic IP (in use) | $0 |
| Route 53 hosted zone | $0.50 |
| ACM cert | $0 |
| Data transfer (~10 GB out/mo) | ~$1 |
| Ollama cloud LLM credits | varies by usage |
| **Total — minimum demo** | **~$35/mo** + LLM usage |
| **Total — recommended** | **~$65/mo** + LLM usage |

Use **Reserved instances** for 30-50% savings if running long-term.

---

## 13. Common AWS pitfalls

| Symptom | Fix |
|---|---|
| `docker: permission denied` after install | `newgrp docker` or re-login |
| `502 Bad Gateway` from nginx | Backend not on :8000 yet — `docker compose ps` to check |
| `502` only on long queries | Increase nginx `proxy_read_timeout` (already set to 300s) |
| Ollama returns 401 on cloud models | Run `./deploy.sh setup-models` again to re-login |
| OAuth redirect mismatch error from GitHub | `GITHUB_REDIRECT_URI` in `.env` doesn't match the OAuth app's callback URL |
| ChromaDB "no collection" after restart | Volume not mounted; verify `docker volume ls` shows `aiagent_chroma_data` |
| Out of disk space | `docker system prune -af --volumes` (careful: removes anonymous volumes) |
| Cert auto-renewal fails | Confirm port 80 inbound is still open in security group |

---

## 14. Pre-deploy verification checklist

Before running `./deploy.sh` for the first time:

- [ ] EC2 instance launched, SSH works
- [ ] Elastic IP allocated + associated
- [ ] Domain A record points at the Elastic IP
- [ ] Docker + Docker Compose v2 installed
- [ ] Repo cloned, `cd Infinium/AI-Agent`
- [ ] `.env` filled with **strong** `JWT_SECRET`, `GITHUB_WEBHOOK_SECRET`, real OAuth creds
- [ ] `chmod 600 .env`
- [ ] Security group: 22 (your IP), 80, 443 open. 8000/11434 closed
- [ ] (Optional) Postgres password set if you enabled it
- [ ] (Optional) Slack signing secret + bot token if Slack integration is in scope

After deploy:

- [ ] `curl https://<your-domain>/api/health` → `"healthy"`
- [ ] OAuth flow works at `https://<your-domain>/api/auth/github`
- [ ] Test ingest of a small public repo via `/api/ingest/url`
- [ ] Test `/api/query` returns an answer
- [ ] Slack `/api/slack/test` returns a formatted message (if configured)
- [ ] Dashboard `/api/dashboard` returns user payload (after login)
- [ ] CloudWatch alarms armed (optional)
- [ ] EBS snapshot lifecycle policy active

You're production-deployable when every box above is ticked.
