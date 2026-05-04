# Infinium — How to Test

This guide walks through every functional requirement (FR-001 … FR-013) and shows
exactly which endpoint to hit, what input to send, and what to expect back.

All endpoints live under `http://localhost:8000` once the backend is running.

---

## 0. Prerequisites

```bash
# 1. Install Ollama + pull models
brew install ollama
ollama serve &
ollama pull nomic-embed-text
ollama run minimax-m2.7:cloud      # requires free ollama.com account
ollama run kimi-k2.5:cloud

# 2. Install Python deps
cd AI-Agent
pip install -r requirements.txt

# 3. Configure .env (copy from .env.example, fill GitHub OAuth + JWT_SECRET)
cp .env.example .env

# 4. Start the API
python run.py            # or: uvicorn backend.api.main:app --reload
```

Visit **`http://localhost:8000/docs`** for the interactive Swagger UI — every
endpoint below is testable from there.

Sanity check:
```bash
curl http://localhost:8000/api/health
# → {"status":"healthy", "ollama":"connected (...)", "chromadb":"ok (...)"}
```

---

## FR-001 — User Authentication (GitHub OAuth)

| Endpoint | Method | Description |
|---|---|---|
| `/api/auth/github` | GET | Redirects to GitHub OAuth |
| `/api/auth/github/callback` | GET | OAuth callback; returns JWT |
| `/api/auth/me` | GET (auth) | Logged-in user profile |
| `/api/auth/github/repos` | GET (auth) | List the user's GitHub repos |
| `/api/auth/logout` | POST (auth) | No-op (stateless) |

**Test:**
1. In a browser, go to `http://localhost:8000/api/auth/github`.
2. Approve the app on GitHub.
3. The callback returns:
   ```json
   { "token": "eyJhbGciOi…", "user": { "login": "...", "name": "..." } }
   ```
4. Save the `token` — every authenticated curl below uses it as
   `Authorization: Bearer <token>`.

---

## FR-002 / FR-003 — Repository Access Validation

| Endpoint | Method | Description |
|---|---|---|
| `/api/access/{public,private}` | POST | Pre-flight check before adding a repo |

**Public repo (FR-003 — no auth needed):**
```bash
curl -X POST http://localhost:8000/api/access/public \
  -H "Content-Type: application/json" \
  -d '{"repo_url":"https://github.com/tiangolo/fastapi"}'
# → {"allowed":true,"is_public":true,"permission":"read",...}
# Sending a private URL here returns 400 with a hint to use /api/access/private.
```

**Private repo (FR-002):**
```bash
curl -X POST http://localhost:8000/api/access/private \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{"repo_url":"https://github.com/your-org/private-repo"}'
# → {"allowed":true,"private":true,"permission":"read"|"write"|"admin"}

# Or via the Node.js backend path:
curl -X POST http://localhost:8000/api/access/private \
  -H "X-GitHub-Token: ghp_..." \
  -H "Content-Type: application/json" \
  -d '{"repo_url":"https://github.com/your-org/private-repo"}'

# Or with the token directly in the body (quick CLI testing):
curl -X POST http://localhost:8000/api/access/private \
  -H "Content-Type: application/json" \
  -d '{"repo_url":"https://github.com/your-org/private-repo","github_token":"ghp_..."}'
# Sending a public URL here returns 400 with a hint to use /api/access/public.
```

---

## FR-004 — On-Demand Repository Scanning

| Endpoint | Method | Description |
|---|---|---|
| `/api/ingest/private` | POST (auth) | Clone + index a **private** GitHub repo |
| `/api/ingest/public` | POST | Index a **public** GitHub repo via REST API (no auth) |
| `/api/ingest/local` | POST | Index a local folder (no clone) |
| `/api/repos/{repo}/status` | GET | Live progress for a single repo |

**Quickest test — index this very repo:**
```bash
curl -X POST http://localhost:8000/api/ingest/local \
  -H "Content-Type: application/json" \
  -d '{"folder_path":"/Users/fizaamjad/Infinium/AI-Agent","repo_name":"infinium-self"}'

# Watch progress:
watch -n 1 'curl -s http://localhost:8000/api/repos/infinium-self/status'
# → status flows: scanning_files → extracting_branches → extracting_diffs → chunking → embedding → complete
```

**Public repo via REST API (no clone, no auth):**
```bash
curl -X POST http://localhost:8000/api/ingest/public \
  -H "Content-Type: application/json" \
  -d '{"repo_url":"https://github.com/tiangolo/fastapi","repo_name":"fastapi"}'
```

**Private repo (clone, auth required):**
```bash
# Using JWT (browser/IDE flow):
curl -X POST http://localhost:8000/api/ingest/private \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{"repo_url":"https://github.com/your-org/private-repo","repo_name":"private"}'

# Using X-GitHub-Token header (Node.js backend flow):
curl -X POST http://localhost:8000/api/ingest/private \
  -H "X-GitHub-Token: ghp_..." \
  -H "X-User-Login: fizaamjad897" \
  -H "Content-Type: application/json" \
  -d '{"repo_url":"https://github.com/your-org/private-repo","repo_name":"private"}'
```

---

## FR-005 — Incremental Data Updates (GitHub webhook)

| Endpoint | Method | Description |
|---|---|---|
| `/api/webhook/github` | POST | Receives `push` / `pull_request` / `issues` events |

**Test locally without a GitHub PR** — fake a push event:
```bash
curl -X POST http://localhost:8000/api/webhook/github \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Event: push" \
  -d '{"repository":{"full_name":"your-org/your-repo","clone_url":"https://github.com/your-org/your-repo.git"}}'
# → {"status":"sync_triggered","event":"push","repo":"your-org_your-repo"}
```

For real webhooks: GitHub repo → Settings → Webhooks → Payload URL =
`https://<your-tunnel>/api/webhook/github`, content-type `application/json`,
secret = `GITHUB_WEBHOOK_SECRET`.

---

## FR-006 / FR-007 — Natural-Language Query + Contextual Response

| Endpoint | Method | Description |
|---|---|---|
| `/api/query` | POST | Single-repo Q&A |
| `/api/query/all` | POST | Cross-repo Q&A |
| `/api/query/git` | POST | Git-history-only Q&A |
| `/api/conversation/start` | POST | Start a multi-turn session |
| `/api/conversation/{id}/history` | GET | View a conversation |

**Single-repo query:**
```bash
curl -X POST http://localhost:8000/api/query \
  -H "Content-Type: application/json" \
  -d '{"query":"How is authentication handled?","repo_name":"infinium-self"}'
# → answer + sources[] + reasoning_trace
```

**Cross-repo query:**
```bash
curl -X POST http://localhost:8000/api/query/all \
  -H "Content-Type: application/json" \
  -d '{"query":"Where do we use FastAPI background tasks?"}'
```

**Multi-turn follow-up:**
```bash
CID=$(curl -s -X POST http://localhost:8000/api/conversation/start \
  -H "Content-Type: application/json" \
  -d '{"repo_name":"infinium-self"}' | jq -r .conversation_id)

curl -X POST http://localhost:8000/api/query \
  -H "Content-Type: application/json" \
  -d "{\"query\":\"What models does the system use?\",\"repo_name\":\"infinium-self\",\"conversation_id\":\"$CID\"}"

curl -X POST http://localhost:8000/api/query \
  -H "Content-Type: application/json" \
  -d "{\"query\":\"And which one is the default?\",\"repo_name\":\"infinium-self\",\"conversation_id\":\"$CID\"}"
```

---

## FR-008 — Feedback Loop

| Endpoint | Method | Description |
|---|---|---|
| `/api/query/feedback` | POST | Submit a 1-5 rating + comment |
| `/api/query/feedback/stats` | GET | Aggregated stats |

```bash
curl -X POST http://localhost:8000/api/query/feedback \
  -H "Content-Type: application/json" \
  -d '{"query":"How is auth handled?","answer":"...","rating":5,"comment":"spot on","repo_name":"infinium-self"}'

curl http://localhost:8000/api/query/feedback/stats
# → {"total":N,"average_rating":4.5,"breakdown":{...}}
```

---

## FR-009 — In-IDE Contextual Assistance

### Backend endpoints (consumed by the VS Code extension):
| Endpoint | Method | Description |
|---|---|---|
| `/api/ide/query` | POST | Free-form question with optional code selection |
| `/api/ide/explain` | POST | "What does this code do?" on a selection |
| `/api/ide/improve` | POST | Code review on a selection |
| `/api/ide/repos` | GET | Lightweight repo list for the picker |

```bash
curl -X POST http://localhost:8000/api/ide/query \
  -H "Content-Type: application/json" \
  -d '{"query":"What does this do?","selected_code":"def add(a,b): return a+b","language":"python"}'
```

### VS Code extension (`/vscode-extension`)
```bash
cd vscode-extension
npm install
npm run compile
# In VS Code: open the folder, press F5 → an Extension Development Host opens.
# In the new window:
#   ⌘⇧P → "Infinium: Choose default repository" → pick one
#   Highlight a function → right-click → "Infinium: Explain selected code"
#   ⌘⇧P → "Infinium: Ask a question" → type any question
```

The answer renders in a side panel with clickable sources.

---

## FR-010 — Slack Bot Integration

| Endpoint | Method | Description |
|---|---|---|
| `/api/slack/command` | POST | Slack slash-command receiver |
| `/api/slack/events` | POST | Slack Events API receiver (handles `app_mention` + URL verification) |
| `/api/slack/test` | POST | **Local-only smoke test — no Slack required** |

**Local smoke test (recommended first step):**
```bash
curl -X POST http://localhost:8000/api/slack/test \
  -H "Content-Type: application/json" \
  -d '{"query":"How is authentication handled?","repo_name":"infinium-self"}'
# → {"slack_text":"<formatted message>","raw_answer":"...","sources":[...]}
```

**Real Slack setup (one-time):**
1. Create a Slack app at <https://api.slack.com/apps>.
2. Copy `Signing Secret` → `SLACK_SIGNING_SECRET` in `.env`.
3. Install app to workspace → copy Bot Token → `SLACK_BOT_TOKEN` in `.env`.
4. Tunnel your local port: `ngrok http 8000` (or run on a public host).
5. **Slash Command** — Request URL: `https://<tunnel>/api/slack/command`,
   command name: `/infinium`.
6. **Event Subscriptions** — Request URL: `https://<tunnel>/api/slack/events`,
   subscribe to bot events: `app_mention`.
7. In Slack: `/infinium how is auth handled?` or
   `/infinium repo:my-repo why was JWT chosen?` or `@Infinium why...`.

---

## FR-011 — Personal Repository Dashboard

All endpoints require auth (`Authorization: Bearer $JWT`).

| Endpoint | Method | Description |
|---|---|---|
| `/api/dashboard` | GET | All-in-one payload (user, repos, stats, history, timeseries, feedback) |
| `/api/dashboard/repos` | GET | Repos owned by the user |
| `/api/dashboard/stats` | GET | Aggregated query analytics |
| `/api/dashboard/history` | GET | Recent queries |
| `/api/dashboard/timeseries` | GET | Queries-per-day series |

```bash
curl http://localhost:8000/api/dashboard \
  -H "Authorization: Bearer $JWT" | jq .
```

Sample shape:
```json
{
  "user": {"login":"...", "name":"..."},
  "repos": [{"repo_name":"...","status":"complete","chunks":1234,...}],
  "stats": {"total_queries":42,"queries_last_7_days":9,"avg_duration_ms":3200,
            "by_repo":{...},"by_interface":{"api":35,"ide":5,"slack":2},
            "top_keywords":[{"keyword":"auth","count":7}]},
  "recent_queries": [...],
  "timeseries": [{"date":"2026-04-19","count":3},...],
  "feedback": {"total":..., "average_rating":...}
}
```

---

## FR-012 — Automated Documentation Summarization

| Endpoint | Method | Description |
|---|---|---|
| `/api/docs/frequent` | GET | Top recurring questions |
| `/api/docs/gaps` | GET | Questions the system couldn't answer |
| `/api/docs/summarize` | POST | LLM-drafted FAQ for a repo |
| `/api/docs/summarize/{repo}` | POST | Convenience wrapper |

**Test flow:**
1. Run a handful of queries against `infinium-self` (so there's history to mine).
2. Ask for the FAQ:
   ```bash
   curl -X POST http://localhost:8000/api/docs/summarize/infinium-self
   # → {"draft":"# infinium-self — Frequently Asked Questions\n\n...","questions_used":[...]}
   ```
3. Inspect documentation gaps:
   ```bash
   curl http://localhost:8000/api/docs/gaps | jq .
   ```

---

## FR-013 — Repository Management Controls

| Endpoint | Method | Description |
|---|---|---|
| `/api/repos/` | GET | List all repos |
| `/api/repos/{repo}/status` | GET | Status detail (chunks, files, commits, branches) |
| `/api/repos/{repo}` | DELETE (auth) | Remove repo + its embeddings |
| `/api/repos/{repo}/reindex` | POST (auth) | Re-run ingestion |
| `/api/repos/collections/list` | GET | Raw ChromaDB collection names |
| `/api/repos/{repo}/branches` | GET | All indexed branches |
| `/api/repos/{repo}/commits?branch=...&limit=N` | GET | Indexed commits |

```bash
# List
curl http://localhost:8000/api/repos/ | jq .

# Status
curl http://localhost:8000/api/repos/infinium-self/status

# Reindex (auth required)
curl -X POST http://localhost:8000/api/repos/infinium-self/reindex \
  -H "Authorization: Bearer $JWT"

# Delete (auth required)
curl -X DELETE http://localhost:8000/api/repos/infinium-self \
  -H "Authorization: Bearer $JWT"
```

---

## End-to-end happy path (5 minutes)

```bash
# 1. boot
ollama serve &
cd AI-Agent && python run.py &

# 2. ingest
curl -X POST http://localhost:8000/api/ingest/local \
  -H "Content-Type: application/json" \
  -d '{"folder_path":"'"$PWD"'","repo_name":"smoke-test"}'

# wait for "complete"
until curl -s http://localhost:8000/api/repos/smoke-test/status | grep -q '"status": "complete"'; do sleep 2; done

# 3. ask
curl -X POST http://localhost:8000/api/query \
  -H "Content-Type: application/json" \
  -d '{"query":"What does the ingestion pipeline do?","repo_name":"smoke-test"}' | jq .answer

# 4. give feedback
curl -X POST http://localhost:8000/api/query/feedback \
  -H "Content-Type: application/json" \
  -d '{"query":"...","answer":"...","rating":5,"repo_name":"smoke-test"}'

# 5. dashboard (after logging in via /api/auth/github)
curl -H "Authorization: Bearer $JWT" http://localhost:8000/api/dashboard | jq .

# 6. summarize the recurring questions
curl -X POST http://localhost:8000/api/docs/summarize/smoke-test | jq .draft
```

---

## Endpoint matrix

| FR | Module | Endpoint(s) | Auth |
|---|---|---|---|
| FR-001 | `auth/github_oauth.py` | `/api/auth/github`, `/api/auth/github/callback`, `/api/auth/me`, `/api/auth/logout`, `/api/auth/github/repos` | OAuth → JWT |
| FR-002 | `routes/access.py` | `/api/access/private` | JWT or X-GitHub-Token |
| FR-003 | `routes/access.py` | `/api/access/public` | none |
| FR-004 | `routes/ingest.py`, `routes/remote.py` | `/api/ingest/private` (auth), `/api/ingest/public` (none), `/api/ingest/local` | mixed |
| FR-005 | `routes/ingest.py` | `/api/webhook/github` | HMAC sig |
| FR-006 / FR-007 | `routes/query.py`, `routes/git_history.py` | `/api/query`, `/api/query/all`, `/api/query/git`, `/api/conversation/*` | none |
| FR-008 | `routes/query.py` | `/api/query/feedback`, `/api/query/feedback/stats` | optional JWT |
| FR-009 | `routes/ide.py` + `vscode-extension/` | `/api/ide/query`, `/api/ide/explain`, `/api/ide/improve`, `/api/ide/repos` | optional JWT |
| FR-010 | `routes/slack.py` | `/api/slack/command`, `/api/slack/events`, `/api/slack/test` | Slack signing |
| FR-011 | `routes/dashboard.py` | `/api/dashboard`, `/api/dashboard/repos`, `/api/dashboard/stats`, `/api/dashboard/history`, `/api/dashboard/timeseries` | JWT |
| FR-012 | `routes/docs.py` | `/api/docs/frequent`, `/api/docs/gaps`, `/api/docs/summarize`, `/api/docs/summarize/{repo}` | none |
| FR-013 | `routes/repos.py` | `/api/repos/`, `/api/repos/{repo}/status`, `/api/repos/{repo}` (DELETE), `/api/repos/{repo}/reindex` | JWT for mutations |

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `disconnected (...)` in `/api/health` | Ollama not running | `ollama serve` |
| Ingestion hangs at `embedding` | `nomic-embed-text` not pulled | `ollama pull nomic-embed-text` |
| 401 on `/api/ingest` | Missing/expired JWT | Re-login via `/api/auth/github` |
| Slack signature 401 | Body mutated by proxy | Set `SLACK_SIGNING_SECRET="" ` for local testing |
| VS Code extension says HTTP error | Backend not on `http://localhost:8000` | Update `infinium.apiUrl` in settings |
| Dashboard shows no repos | You ingested before adding `ingested_by` field | Re-ingest while logged-in, or all repos remain visible to all users |
