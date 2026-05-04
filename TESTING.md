# Infinium AI Agent — Testing Guide

A traceable, FR-by-FR verification plan for the Infinium AI agent. Every
functional requirement maps to a concrete endpoint, an input, an expected
output, and a one-command test you can run.

> **Companion file:** see `DEPLOYMENT.md` for getting the agent running on
> AWS. See `HOW_TO_TEST.md` (kept for backwards-compat) for a shorter
> quick-reference.

---

## 0. Test environment setup

### 0a. Local (recommended for development testing)

```bash
# 1. Ollama running with models
ollama serve &
ollama pull nomic-embed-text
ollama run minimax-m2.7:cloud
ollama run kimi-k2.5:cloud

# 2. Backend running
cd AI-Agent
pip install -r requirements.txt
python run.py            # listens on http://localhost:8000
```

Or with Docker:
```bash
cd AI-Agent
docker compose up -d
```

### 0b. Remote (AWS / staging)

```bash
export INFINIUM_HOST=https://api.infinium.io     # change to your URL
curl $INFINIUM_HOST/api/health
```

Below, every command uses `$INFINIUM_HOST`. Set it once.
For local testing: `export INFINIUM_HOST=http://localhost:8000`.

### 0c. Get a JWT for authenticated tests

```bash
# Browser:
open "$INFINIUM_HOST/api/auth/github"
# After consent, copy the "token" from the response JSON.
export JWT="eyJhbGciOi..."
```

### 0d. Sanity check before running anything

```bash
curl $INFINIUM_HOST/api/health
# Expect:
# {"status":"healthy","ollama":"connected (...)","chromadb":"ok (...)",
#  "config":{"llm_model":"...","embed_model":"...","github_oauth_configured":true}}
```

If `github_oauth_configured: false`, FR-001 tests below cannot pass — fix `.env` first.

---

## 1. FR-001 — User Authentication (GitHub OAuth)

**Description:** Authenticate via GitHub OAuth, return JWT.
**Priority:** High

### 1a. Inputs / outputs

| Step | Input | Endpoint | Expected output |
|---|---|---|---|
| 1 | (none) | `GET /api/auth/github` | 302 → github.com/login/oauth/authorize |
| 2 | `code` from GitHub callback | `GET /api/auth/github/callback?code=...` | `{"token": "...", "user": {...}}` |
| 3 | JWT bearer | `GET /api/auth/me` | `{"login": "...", ...}` |
| 4 | JWT bearer | `GET /api/auth/github/repos` | List of user's GitHub repos |
| 5 | JWT bearer | `POST /api/auth/logout` | `{"message": "Logged out successfully"}` |

### 1b. Manual test (full OAuth flow)

```bash
# Step 1: open in browser
open "$INFINIUM_HOST/api/auth/github"
# Approve on GitHub → callback returns JWT JSON.

# Step 3: verify JWT
curl -H "Authorization: Bearer $JWT" $INFINIUM_HOST/api/auth/me
# → {"login":"fizaamjad897","name":"...","avatar_url":"...","github_id":...}

# Step 4: list user's GitHub repos
curl -H "Authorization: Bearer $JWT" $INFINIUM_HOST/api/auth/github/repos | jq '.[0:3]'
```

### 1c. Pass criteria

- ✅ Step 2 returns a JWT signed with `JWT_SECRET`.
- ✅ Step 3 returns the same `login` you authenticated with.
- ✅ Without `Authorization` header, `/api/auth/me` returns **401** "Not authenticated".
- ✅ Tampered JWT (modify a char) returns **401** "Invalid token".
- ✅ Expired JWT returns **401** "Token has expired".

### 1d. Failure cases to test

```bash
# No token
curl -i $INFINIUM_HOST/api/auth/me   # → 401

# Bad token
curl -i -H "Authorization: Bearer not.a.jwt" $INFINIUM_HOST/api/auth/me   # → 401
```

---

## 2. FR-002 / FR-003 — Repository Access Validation (split endpoints)

**Description:** Validate user has read access (FR-002, private endpoint);
allow public repos without auth (FR-003, public endpoint). Each endpoint
strictly handles only its category and rejects the other with a redirect hint.

**Priority:** High / Medium

### 2a. Endpoints

| Endpoint | Auth | Purpose |
|---|---|---|
| `POST /api/access/public` | none | Validate a public repo. Rejects private URLs with a 400. |
| `POST /api/access/private` | JWT or X-GitHub-Token or `github_token` in body | Validate a private repo. Rejects public URLs with a 400. |

### 2b. Public-endpoint tests (FR-003)

```bash
# Happy path — public repo
curl -X POST $INFINIUM_HOST/api/access/public \
  -H "Content-Type: application/json" \
  -d '{"repo_url":"https://github.com/tiangolo/fastapi"}'
# → {"allowed":true,"is_public":true,"permission":"read",...}

# Wrong category — private URL hits the public endpoint
curl -i -X POST $INFINIUM_HOST/api/access/public \
  -H "Content-Type: application/json" \
  -d '{"repo_url":"https://github.com/your-org/private-repo"}'
# → 400 "...is a PRIVATE repository. ... Use /api/access/private instead."

# Bad URL
curl -i -X POST $INFINIUM_HOST/api/access/public \
  -H "Content-Type: application/json" \
  -d '{"repo_url":"not-a-url"}'
# → 400 "Invalid GitHub URL"
```

### 2c. Private-endpoint tests (FR-002)

```bash
# Happy path — JWT (browser/IDE flow)
curl -X POST $INFINIUM_HOST/api/access/private \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{"repo_url":"https://github.com/your-org/private-repo"}'
# → {"allowed":true,"private":true,"permission":"read|write|admin"}

# Happy path — Node.js backend flow
curl -X POST $INFINIUM_HOST/api/access/private \
  -H "X-GitHub-Token: ghp_..." \
  -H "Content-Type: application/json" \
  -d '{"repo_url":"https://github.com/your-org/private-repo"}'

# Happy path — token directly in body (quick CLI test)
curl -X POST $INFINIUM_HOST/api/access/private \
  -H "Content-Type: application/json" \
  -d '{"repo_url":"https://github.com/your-org/private-repo","github_token":"ghp_..."}'

# No auth at all
curl -i -X POST $INFINIUM_HOST/api/access/private \
  -H "Content-Type: application/json" \
  -d '{"repo_url":"https://github.com/your-org/private-repo"}'
# → 401 "Auth required for private-repo validation..."

# Wrong category — public URL hits the private endpoint
curl -i -X POST $INFINIUM_HOST/api/access/private \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{"repo_url":"https://github.com/tiangolo/fastapi"}'
# → 400 "...is a PUBLIC repository. Use /api/access/public ..."
```

### 2d. Pass criteria

- ✅ `/api/access/public` returns 200 for public repos with **no auth**.
- ✅ `/api/access/public` returns 400 for private URLs with a redirect hint.
- ✅ `/api/access/private` returns 200 for private repos with valid auth and the actual `permission` (read/write/admin).
- ✅ `/api/access/private` returns 401 with no auth at all.
- ✅ `/api/access/private` returns 400 for public URLs with a redirect hint.
- ✅ `/api/access/private` accepts auth via *any* of the three paths (JWT / X-GitHub-Token / body field).

---

## 3. FR-004 — On-Demand Repository Scanning

**Description:** Begin immediate ingestion when a repo is added.
**Priority:** High

### 3a. Three ingestion paths (split by repo visibility)

| Path | Endpoint | When to use |
|---|---|---|
| **Public repo** (REST API, no clone, no auth) | `POST /api/ingest/public` | Any public GitHub repo |
| **Private repo** (clone, auth required) | `POST /api/ingest/private` | Your own / org private repos |
| Local folder | `POST /api/ingest/local` | Already-cloned repo on disk; testing |

### 3b. Test — local folder (fastest)

```bash
# Ingest this very repo
curl -X POST $INFINIUM_HOST/api/ingest/local \
  -H "Content-Type: application/json" \
  -d '{"folder_path":"/abs/path/to/Infinium/AI-Agent","repo_name":"smoke-test"}'
# → {"status":"ingestion_started","repo_name":"smoke-test","message":"..."}

# Watch progress (replace 'watch' with a polling loop on macOS):
while true; do
  STATUS=$(curl -s $INFINIUM_HOST/api/repos/smoke-test/status | jq -r .status)
  echo "[$(date +%T)] $STATUS"
  [ "$STATUS" = "complete" ] && break
  [ "$STATUS" = "error" ] && break
  sleep 2
done
```

Status flow: `scanning_files` → `extracting_branches` → `extracting_diffs`
→ `chunking` → `embedding` → `complete`.

### 3c. Test — private repo (Node.js backend flow)

```bash
curl -X POST $INFINIUM_HOST/api/ingest/private \
  -H "X-GitHub-Token: ghp_yourPersonalAccessToken" \
  -H "X-User-Login: fizaamjad897" \
  -H "Content-Type: application/json" \
  -d '{"repo_url":"https://github.com/your-org/private-repo","repo_name":"private"}'
```

Or via JWT (browser/IDE flow):
```bash
curl -X POST $INFINIUM_HOST/api/ingest/private \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{"repo_url":"https://github.com/your-org/private-repo","repo_name":"private"}'
```

### 3d. Test — public repo (no auth)

```bash
curl -X POST $INFINIUM_HOST/api/ingest/public \
  -H "Content-Type: application/json" \
  -d '{"repo_url":"https://github.com/tiangolo/fastapi","repo_name":"fastapi-public"}'
```

Sending a private URL here returns 400; sending a public URL to `/api/ingest/private` also returns 400.

### 3e. Pass criteria

- ✅ Status reaches `complete` (or `error` with a readable message).
- ✅ `chunks_in_db > 0` after `complete`:
  ```bash
  curl $INFINIUM_HOST/api/repos/smoke-test/status | jq .chunks_in_db
  ```
- ✅ The repo appears in `GET /api/repos/`.

### 3f. Failure cases

```bash
# Local: nonexistent folder → 404
curl -i -X POST $INFINIUM_HOST/api/ingest/local \
  -H "Content-Type: application/json" \
  -d '{"folder_path":"/does/not/exist","repo_name":"x"}'

# Auth: ingest of a private repo without any token → status reaches "error"
```

---

## 4. FR-005 — Incremental Data Updates (GitHub webhook)

**Description:** Auto-sync repos via webhook events.
**Priority:** Medium

### 4a. Endpoint
`POST /api/webhook/github` — accepts `push`, `pull_request`, `issues` events;
HMAC-signed when `GITHUB_WEBHOOK_SECRET` is set.

### 4b. Test (no signature, dev mode)

Temporarily empty `GITHUB_WEBHOOK_SECRET` in `.env`, restart, then:

```bash
curl -X POST $INFINIUM_HOST/api/webhook/github \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Event: push" \
  -d '{"repository":{"full_name":"your-org/your-repo","clone_url":"https://github.com/your-org/your-repo.git"}}'
# → {"status":"sync_triggered","event":"push","repo":"your-org_your-repo"}
```

### 4c. Test (with signature, production)

```bash
SECRET=$(grep GITHUB_WEBHOOK_SECRET .env | cut -d= -f2)
BODY='{"repository":{"full_name":"your-org/your-repo","clone_url":"https://github.com/your-org/your-repo.git"}}'
SIG="sha256=$(echo -n "$BODY" | openssl dgst -sha256 -hmac "$SECRET" | cut -d' ' -f2)"

curl -X POST $INFINIUM_HOST/api/webhook/github \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Event: push" \
  -H "X-Hub-Signature-256: $SIG" \
  -d "$BODY"
# → {"status":"sync_triggered",...}
```

Bad signature returns 401.

### 4d. Real GitHub setup
1. Repo → Settings → Webhooks → Add webhook
2. Payload URL: `$INFINIUM_HOST/api/webhook/github`
3. Content type: `application/json`
4. Secret: same as `GITHUB_WEBHOOK_SECRET`
5. Events: `push`, `pull_request`, `issues`
6. Push a commit → check `/api/repos/your-org_your-repo/status`.

### 4e. Pass criteria
- ✅ Valid signature triggers re-ingest.
- ✅ Invalid signature returns 401.
- ✅ Unsupported event returns `{"status":"ignored"}`.

---

## 5. FR-006 / FR-007 — Natural-Language Query + Contextual Response

**Description:** NL question → retrieve context → synthesise answer with sources.
**Priority:** High

### 5a. Endpoints

| Endpoint | Scope |
|---|---|
| `POST /api/query` | Single repo |
| `POST /api/query/all` | All indexed repos |
| `POST /api/query/git` | Git history only (commits/diffs/branches) |

### 5b. Test — single-repo query

```bash
curl -X POST $INFINIUM_HOST/api/query \
  -H "Content-Type: application/json" \
  -d '{"query":"How is authentication handled?","repo_name":"smoke-test"}' | jq
```

Verify the response contains:
- `answer` — multi-paragraph natural language
- `sources` — array with `source` (file path) and `relevance` ≥ 0
- `model` — the LLM that produced it
- `chunks_retrieved` — number of context chunks used
- `total_duration_ms` — should be < 5000 (FR NFR: < 5 s)
- `reasoning_trace` — step-by-step trace for explainability

### 5c. Test — cross-repo

```bash
curl -X POST $INFINIUM_HOST/api/query/all \
  -H "Content-Type: application/json" \
  -d '{"query":"Where do we use FastAPI background tasks?"}' | jq '.sources[].repo' | sort -u
# → multiple repos in the source list
```

### 5d. Test — git history query

```bash
curl -X POST $INFINIUM_HOST/api/query/git \
  -H "Content-Type: application/json" \
  -d '{"query":"What changed in the last 5 commits?","repo_name":"smoke-test"}'
```

### 5e. Test — multi-turn conversation

```bash
CID=$(curl -s -X POST $INFINIUM_HOST/api/conversation/start \
  -H "Content-Type: application/json" \
  -d '{"repo_name":"smoke-test"}' | jq -r .conversation_id)

# Q1
curl -X POST $INFINIUM_HOST/api/query \
  -H "Content-Type: application/json" \
  -d "{\"query\":\"What models does the system use?\",\"repo_name\":\"smoke-test\",\"conversation_id\":\"$CID\"}" | jq .answer

# Q2 — follow-up references "the default" without restating
curl -X POST $INFINIUM_HOST/api/query \
  -H "Content-Type: application/json" \
  -d "{\"query\":\"Which one is the default?\",\"repo_name\":\"smoke-test\",\"conversation_id\":\"$CID\"}" | jq .answer

# Inspect the full thread
curl $INFINIUM_HOST/api/conversation/$CID/history | jq
```

### 5f. Pass criteria

- ✅ Answer is coherent natural language with at least one `[Source N: file.py]`-style citation.
- ✅ `sources` is non-empty for indexed content.
- ✅ Q2 in the conversation references context from Q1 (i.e. understands "the default" in context).
- ✅ Cross-repo query returns sources from at least 2 repos when 2+ are indexed.
- ✅ Latency `total_duration_ms < 5000` for single-model mode (NFR Performance).

### 5g. Edge cases

```bash
# Empty repo
curl -X POST $INFINIUM_HOST/api/query \
  -H "Content-Type: application/json" \
  -d '{"query":"foo","repo_name":"nonexistent"}'
# → answer: "I couldn't find relevant context...", chunks_retrieved: 0
```

---

## 6. FR-008 — Feedback Loop

**Description:** Collect rating + comment per answer; aggregate stats.
**Priority:** Medium

### 6a. Endpoints
| Endpoint | Method | Purpose |
|---|---|---|
| `/api/query/feedback` | POST | Submit 1-5 rating + comment |
| `/api/query/feedback/stats` | GET | Aggregated breakdown |

### 6b. Tests

```bash
# Submit a 5-star rating
curl -X POST $INFINIUM_HOST/api/query/feedback \
  -H "Content-Type: application/json" \
  -d '{"query":"How is auth handled?","answer":"...","rating":5,"comment":"spot on","repo_name":"smoke-test"}'
# → {"status":"feedback_saved","entry":{...,"rating":5,"id":N}}

# Submit a 1-star rating
curl -X POST $INFINIUM_HOST/api/query/feedback \
  -H "Content-Type: application/json" \
  -d '{"query":"x","answer":"y","rating":1,"comment":"wrong","repo_name":"smoke-test"}'

# Stats
curl $INFINIUM_HOST/api/query/feedback/stats | jq
# → {"total":2,"average_rating":3.0,"breakdown":{"1":1,"2":0,"3":0,"4":0,"5":1},"recent":[...]}
```

### 6c. Pass criteria

- ✅ `total` matches the count of submitted feedbacks.
- ✅ `average_rating` matches arithmetic mean.
- ✅ `breakdown` distributes ratings 1-5 correctly.
- ✅ Out-of-range rating (e.g. 6) is rejected with 422.

---

## 7. FR-009 — In-IDE Contextual Assistance

**Description:** Backend endpoint for the VSCode extension.
**Priority:** High

### 7a. Endpoints
| Endpoint | Purpose |
|---|---|
| `POST /api/ide/query` | Free-form query with optional code selection |
| `POST /api/ide/explain` | Implicit "what does this code do?" |
| `POST /api/ide/improve` | Implicit "review this code for issues" |
| `GET /api/ide/repos` | Lightweight repo list for picker |

### 7b. Test — explain selected code

```bash
curl -X POST $INFINIUM_HOST/api/ide/explain \
  -H "Content-Type: application/json" \
  -d '{
    "selected_code": "def fib(n):\n    return n if n<2 else fib(n-1)+fib(n-2)",
    "language": "python",
    "file_path": "/scratch/fib.py"
  }' | jq
```

### 7c. Test — improve

```bash
curl -X POST $INFINIUM_HOST/api/ide/improve \
  -H "Content-Type: application/json" \
  -d '{
    "selected_code":"for i in range(len(arr)):\n    if arr[i]==target: return i",
    "language":"python"
  }' | jq .answer
```

### 7d. Test — VS Code extension end-to-end

```bash
cd vscode-extension
npm install
npm run compile
```

In VS Code:
1. Open the `vscode-extension` folder
2. Press `F5` → Extension Development Host opens
3. In the new window:
   - `⌘⇧P` → "Infinium: Choose default repository" → pick one
   - Highlight a function → right-click → "Infinium: Explain selected code"
   - `⌘⇧P` → "Infinium: Ask a question" → "How is auth handled?"
4. Answer renders in a side panel.

### 7e. Pass criteria

- ✅ `/api/ide/query` returns `answer`, `sources`, `model`, `duration_ms`.
- ✅ Right-click context-menu items appear when text is selected.
- ✅ Answer panel renders with source citations.
- ✅ Selecting a different repo in the picker scopes subsequent queries.

---

## 8. FR-010 — Slack Bot Integration

**Description:** Slack slash command + @-mention bot answers from Infinium.
**Priority:** Medium

### 8a. Endpoints
| Endpoint | Use |
|---|---|
| `/api/slack/command` | Slash command receiver |
| `/api/slack/events` | Events API receiver (app_mention, URL verification) |
| `/api/slack/test` | **Local-only smoke test — no Slack required** |

### 8b. Test — local smoke

```bash
curl -X POST $INFINIUM_HOST/api/slack/test \
  -H "Content-Type: application/json" \
  -d '{"query":"How is authentication handled?","repo_name":"smoke-test"}' | jq
# → {"slack_text":"...formatted markdown...","raw_answer":"...","sources":[...]}
```

The `slack_text` is what would appear in a Slack channel.

### 8c. Test — URL verification challenge

When you set up the Events API in Slack, Slack sends a `url_verification`
challenge. Simulate it:
```bash
curl -X POST $INFINIUM_HOST/api/slack/events \
  -H "Content-Type: application/json" \
  -d '{"type":"url_verification","challenge":"abc123"}'
# → {"challenge":"abc123"}
```

### 8d. Test — full Slack flow

1. Create Slack app at https://api.slack.com/apps
2. **Slash Commands** → New → `/infinium` → request URL `$INFINIUM_HOST/api/slack/command`
3. **Event Subscriptions** → Request URL `$INFINIUM_HOST/api/slack/events` → subscribe to `app_mention`
4. Install to workspace, copy:
   - `Signing Secret` → `.env SLACK_SIGNING_SECRET`
   - `Bot Token` → `.env SLACK_BOT_TOKEN`
5. In Slack:
   - `/infinium how is auth handled?` (slash command)
   - `/infinium repo:my-repo why was JWT chosen?` (scoped slash command)
   - `@Infinium why was JWT chosen?` (mention)

### 8e. Pass criteria

- ✅ Local smoke test returns formatted message.
- ✅ URL verification echoes the challenge back.
- ✅ Slash command initially replies with `🔍 Asking Infinium…` (within Slack's 3s window) and follows up with the real answer.
- ✅ App mention posts a threaded reply in the channel.
- ✅ Bad signature returns 401 (when `SLACK_SIGNING_SECRET` is set).

---

## 9. FR-011 — Personal Repository Dashboard

**Description:** Per-user dashboard with repo cards + query analytics.
**Priority:** High

### 9a. Endpoints (all require JWT)
| Endpoint | Purpose |
|---|---|
| `GET /api/dashboard` | All-in-one home payload |
| `GET /api/dashboard/repos` | User's repos with status |
| `GET /api/dashboard/stats` | Aggregated query analytics |
| `GET /api/dashboard/history` | Recent queries by user |
| `GET /api/dashboard/timeseries` | Queries-per-day series |

### 9b. Tests

```bash
# 1. Login first (FR-001) → $JWT

# 2. Run a few queries while logged in (so history accumulates)
for q in "How is auth handled?" "Where is ChromaDB used?" "What models does it use?"; do
  curl -X POST $INFINIUM_HOST/api/query \
    -H "Authorization: Bearer $JWT" \
    -H "Content-Type: application/json" \
    -d "{\"query\":\"$q\",\"repo_name\":\"smoke-test\"}" > /dev/null
done

# 3. Get dashboard
curl -H "Authorization: Bearer $JWT" $INFINIUM_HOST/api/dashboard | jq
```

Expected shape:
```json
{
  "user":     {"login":"...","name":"...","avatar_url":"..."},
  "repos":    [{"repo_name":"smoke-test","status":"complete","chunks":N,...}],
  "stats":    {"total_queries":N,"queries_last_7_days":N,"avg_duration_ms":N,
               "by_repo":{...},"by_interface":{"api":N,"ide":0,"slack":0},
               "top_keywords":[{"keyword":"auth","count":1},...]},
  "recent_queries":[{...},{...}],
  "timeseries":[{"date":"2026-04-26","count":N},...],
  "feedback":  {"total":N,"average_rating":...}
}
```

### 9c. Pass criteria

- ✅ Without JWT → 401.
- ✅ `repos` only contains repos ingested by the logged-in user (after the FR-002 changes — older ones still visible to all for backwards-compat).
- ✅ `stats.total_queries` increments after each query.
- ✅ `timeseries` has 14 day-buckets.
- ✅ `top_keywords` excludes stopwords.

---

## 10. FR-012 — Automated Documentation Summarisation

**Description:** Mine query history for FAQs, identify gaps.
**Priority:** Low

### 10a. Endpoints
| Endpoint | Purpose |
|---|---|
| `GET /api/docs/frequent` | Top recurring questions |
| `GET /api/docs/gaps` | Questions answered poorly (gap analysis) |
| `POST /api/docs/summarize` | LLM-drafted FAQ for a repo |
| `POST /api/docs/summarize/{repo}` | Convenience wrapper |

### 10b. Tests

```bash
# Step 1 — generate enough history (re-ask the same 3 questions a few times)
for q in "How is auth handled?" "How is auth handled?" "What models?" "What models?"; do
  curl -s -X POST $INFINIUM_HOST/api/query \
    -H "Content-Type: application/json" \
    -d "{\"query\":\"$q\",\"repo_name\":\"smoke-test\"}" > /dev/null
done

# Step 2 — frequent questions
curl $INFINIUM_HOST/api/docs/frequent | jq
# → [{"query":"How is auth handled?","count":2,...}]

# Step 3 — gaps (questions where retrieval failed)
curl $INFINIUM_HOST/api/docs/gaps | jq

# Step 4 — generate FAQ
curl -X POST $INFINIUM_HOST/api/docs/summarize/smoke-test | jq -r .draft
# → '# smoke-test — Frequently Asked Questions\n\n...'
```

### 10c. Pass criteria

- ✅ `frequent` lists questions with `count >= min_count`.
- ✅ `gaps` lists queries where `sources_count == 0`.
- ✅ `summarize` returns Markdown starting with `# {repo} — Frequently Asked Questions`.
- ✅ When there's no history, `summarize` returns an explanatory message instead of failing.

---

## 11. FR-013 — Repository Management Controls

**Description:** Remove, re-index, view detailed status.
**Priority:** Medium

### 11a. Endpoints
| Endpoint | Method | Auth |
|---|---|---|
| `/api/repos/` | GET | none |
| `/api/repos/{repo}/status` | GET | none |
| `/api/repos/{repo}` | DELETE | JWT or X-GitHub-Token |
| `/api/repos/{repo}/reindex` | POST | JWT or X-GitHub-Token |
| `/api/repos/collections/list` | GET | none |
| `/api/repos/{repo}/branches` | GET | none |
| `/api/repos/{repo}/commits?branch=...&limit=N` | GET | none |

### 11b. Tests

```bash
# List
curl $INFINIUM_HOST/api/repos/ | jq
# → [{"repo_name":"smoke-test","status":"complete","chunks":N,...}]

# Detailed status
curl $INFINIUM_HOST/api/repos/smoke-test/status | jq
# → {"status":"complete","files_processed":N,"commits_processed":N,"branches_processed":N,...}

# Branches
curl $INFINIUM_HOST/api/repos/smoke-test/branches | jq

# Commits on a specific branch
curl "$INFINIUM_HOST/api/repos/smoke-test/commits?branch=main&limit=10" | jq

# Reindex (auth required)
curl -X POST $INFINIUM_HOST/api/repos/smoke-test/reindex \
  -H "Authorization: Bearer $JWT"
# → {"status":"reindex_started","repo_name":"smoke-test","message":"..."}

# Delete (auth required)
curl -X DELETE $INFINIUM_HOST/api/repos/smoke-test \
  -H "Authorization: Bearer $JWT"
# → {"status":"removed","repo_name":"smoke-test"}

# Verify gone
curl $INFINIUM_HOST/api/repos/smoke-test/status | jq
# → {"status":"unknown",...}
```

### 11c. Pass criteria

- ✅ DELETE without auth → 401.
- ✅ DELETE removes both `data/ingestion_status.json` entry AND ChromaDB collection.
- ✅ Reindex without auth → 401.
- ✅ Reindex preserves `repo_name`; chunks rebuild from scratch.

---

## 11b. Code-Aware Operations (cross-cutting)

These endpoints span multiple FRs (FR-006, FR-007, FR-009) and address the
core differentiator goals: cross-repo reasoning, find-a-function, code
rewriting, and cross-branch reasoning.

### 11b.1 Find a function or class by name

```bash
# In one repo
curl -X POST $INFINIUM_HOST/api/code/find \
  -H "Content-Type: application/json" \
  -d '{"symbol":"ingest_repository","repo_name":"smoke-test","language":"python"}' | jq

# Across all repos
curl -X POST $INFINIUM_HOST/api/code/find \
  -H "Content-Type: application/json" \
  -d '{"symbol":"get_chroma_collection","language":"python"}' | jq
```

Each hit contains:
- `repo`, `file`, `language`
- `kind` — function | class | method | interface | type | impl
- `matched_line` — the actual `def` / `class` / `function` line
- `snippet` — ~25 lines surrounding the definition
- `relevance` — retrieval score

**Pass criteria:**
- ✅ Known symbols (e.g. `ingest_repository`) return at least one hit with the right file path.
- ✅ Cross-repo search returns hits from multiple repos when the symbol is shared.
- ✅ Unknown symbols return an empty list (not 500).

### 11b.2 Find call-sites of a symbol

```bash
curl -X POST $INFINIUM_HOST/api/code/callers \
  -H "Content-Type: application/json" \
  -d '{"symbol":"ingest_repository","repo_name":"smoke-test"}' | jq
```

**Pass criteria:** returns chunks where the symbol appears as `name(` —
excludes the chunk that contains the definition itself.

### 11b.3 Rewrite a code snippet

```bash
curl -X POST $INFINIUM_HOST/api/code/rewrite \
  -H "Content-Type: application/json" \
  -d '{
    "snippet":"def add(a,b):\n    return a+b",
    "instruction":"add type hints and a docstring",
    "language":"python"
  }' | jq
```

Response:
```json
{
  "rewritten_code": "def add(a: int, b: int) -> int:\n    \"\"\"...\"\"\"\n    return a + b",
  "rationale": "Added int type hints and docstring per PEP-257.",
  "risks": "none.",
  "model": "minimax-m2.7:cloud",
  "context_used": 0,
  "context_sources": []
}
```

With repo context (rewrite stays consistent with repo style):
```bash
curl -X POST $INFINIUM_HOST/api/code/rewrite \
  -H "Content-Type: application/json" \
  -d '{
    "snippet":"for i in range(len(arr)):\n    process(arr[i])",
    "instruction":"convert to a more pythonic loop",
    "language":"python",
    "repo_name":"smoke-test",
    "use_context":true
  }' | jq
```

**Pass criteria:**
- ✅ `rewritten_code` is non-empty and parses as valid code in the language.
- ✅ `rationale` and `risks` lines are present.
- ✅ With `use_context: true`, `context_sources` lists files actually used.

### 11b.4 Explain a snippet (standalone, repo-grounded)

```bash
curl -X POST $INFINIUM_HOST/api/code/explain \
  -H "Content-Type: application/json" \
  -d '{
    "snippet":"async def webhook(request, sig): ...",
    "language":"python",
    "repo_name":"smoke-test"
  }' | jq -r .explanation
```

Differs from `/api/ide/explain` — this works without an active IDE editor
selection and is meant for programmatic callers (Slack, dashboard).

### 11b.5 Cross-repo comparison query

```bash
# Compare auth handling across two repos
curl -X POST $INFINIUM_HOST/api/code/cross-repo \
  -H "Content-Type: application/json" \
  -d '{
    "query":"How is authentication handled?",
    "repo_names":["smoke-test","fastapi-public"]
  }' | jq -r .answer
```

Differs from `/api/query/all` — this **forces** a comparison framing in
the prompt and requires ≥ 2 explicit repos.

**Pass criteria:**
- ✅ `repos_with_results` is a subset of `repos_searched` (at least 2 repos contribute).
- ✅ Answer text mentions each repo by name and explicitly compares them.
- ✅ With < 2 repos in the body → 422 validation error.

### 11b.6 Branch diff (no LLM, pure data)

```bash
curl "$INFINIUM_HOST/api/repos/smoke-test/branches/diff?base=main&head=phase-4" | jq
```

Returns:
- `commits_only_in_base` / `commits_only_in_head`
- `files_changed_on_head` (the "PR file list")
- `authors_on_head`
- `summary` — one-line "N ahead, M behind"

**Pass criteria:**
- ✅ For an unmerged feature branch, `commits_only_in_head` is non-empty.
- ✅ For two identical branches, both lists are empty.
- ✅ For a missing branch name, returns 404.

### 11b.7 Cross-branch reasoning query

```bash
curl -X POST $INFINIUM_HOST/api/query/cross-branch \
  -H "Content-Type: application/json" \
  -d '{
    "query":"What features differ between branches?",
    "repo_name":"smoke-test",
    "branches":["main","phase-3","phase-4"]
  }' | jq
```

Pulls chunks per branch, tags each chunk with `[branch: NAME]`, and asks
the LLM to compare/contrast.

Response includes:
- `answer` — comparison-style narrative
- `branches_searched` — echoes input
- `chunks_per_branch` — how many chunks each branch contributed
- `chunks_retrieved` — total

**Pass criteria:**
- ✅ Each branch in `chunks_per_branch` should have count > 0 (or the branch genuinely has no commits).
- ✅ Answer text references the branches by name and surfaces real differences.
- ✅ With < 2 branches → 422.

### 11b.8 End-to-end "rewrite → review" loop

```bash
# Step 1: find the function
HIT=$(curl -s -X POST $INFINIUM_HOST/api/code/find \
  -H "Content-Type: application/json" \
  -d '{"symbol":"_chunk_and_store","repo_name":"smoke-test"}' | jq '.[0]')

SNIPPET=$(echo $HIT | jq -r .snippet)
FILE=$(echo $HIT | jq -r .file)

# Step 2: rewrite it
curl -X POST $INFINIUM_HOST/api/code/rewrite \
  -H "Content-Type: application/json" \
  -d "$(jq -n --arg s "$SNIPPET" --arg f "$FILE" '{
        snippet:$s,
        instruction:"split into smaller helpers",
        file_path:$f,
        language:"python",
        repo_name:"smoke-test",
        use_context:true
      }')" | jq
```

This is the canonical workflow for "find a function and rewrite it".

---

## 11c. Long-running ingestion — progress, callbacks, timeouts

Ingestion of a 50k-LOC repo can take 5-30 minutes depending on commit count.
The endpoint returns 200 immediately and runs the work in the background.
Three ways to observe progress, in increasing order of efficiency:

| Pattern | Best for | How |
|---|---|---|
| **Polling** `GET /status` | Quick CLI scripts | curl every few seconds |
| **SSE stream** `GET /status/stream` | Browser dashboards, Node services | one persistent HTTP connection |
| **`callback_url`** | Slack notifications, Node webhooks | one POST when done |

### 11c.1 Pre-flight size estimate

Before kicking off ingestion, ask how big it'll be:

```bash
curl -X POST $INFINIUM_HOST/api/repos/estimate \
  -H "Content-Type: application/json" \
  -d '{"repo_url":"https://github.com/tiangolo/fastapi"}' | jq
```

Response:
```json
{
  "repo_full_name": "tiangolo/fastapi",
  "files_total": 850,
  "commits_total_on_branch": 4220,
  "commits_to_index": 200,
  "estimated_chunks": 2760,
  "estimated_seconds": 552,
  "estimated_minutes": 9.2,
  "verdict": "medium",
  "recommendation": "Use a callback_url or SSE stream — synchronous waiting will time out."
}
```

`verdict` is `small` (< 1 min), `medium` (< 10 min), `large` (< 30 min), or `huge`.

### 11c.2 Polling status (works everywhere)

```bash
while true; do
  S=$(curl -s $INFINIUM_HOST/api/repos/smoke/status | jq -r '"\(.status) \(.percent_complete // 0)% (\(.chunks_processed // 0)/\(.chunks_total // 0)) ETA \(.eta_seconds // 0)s"')
  echo "[$(date +%T)] $S"
  echo "$S" | grep -q '^complete\|^error' && break
  sleep 5
done
```

Status now includes `chunks_processed`, `chunks_total`, `percent_complete`,
`embed_rate_per_sec`, and `eta_seconds` while the embedding phase runs —
no more "stuck on embedding for 10 minutes" mystery.

### 11c.3 SSE stream (best for live UIs)

```bash
curl -N $INFINIUM_HOST/api/repos/smoke/status/stream
# data: {"status":"cloning","repo_url":"...","updated_at":"..."}
# data: {"status":"embedding","chunks_processed":50,"chunks_total":2760,"percent_complete":1.8,"eta_seconds":540}
# data: {"status":"embedding","chunks_processed":100,"chunks_total":2760,"percent_complete":3.6,"eta_seconds":520}
# ...
# data: {"status":"complete","chunks_stored":2760,"elapsed_seconds":548}
# event: done
# data: {}
```

In a browser (or Electron / VS Code webview):
```js
const es = new EventSource('http://localhost:8000/api/repos/smoke/status/stream');
es.onmessage = e => updateProgressBar(JSON.parse(e.data));
es.addEventListener('done', () => es.close());
```

In Node.js (with `eventsource` npm package):
```js
import EventSource from 'eventsource';
const es = new EventSource(`${PYTHON_AI_URL}/api/repos/${repo}/status/stream`);
es.onmessage = e => {
  const s = JSON.parse(e.data);
  console.log(`${s.status} ${s.percent_complete ?? ''}%`);
};
es.addEventListener('done', () => es.close());
```

### 11c.4 `callback_url` (notify-on-completion)

Best when you don't want to keep an HTTP connection open. The Node backend
exposes one endpoint that accepts the final status, then kicks off ingestion
with the callback URL set:

```bash
curl -X POST $INFINIUM_HOST/api/ingest/private \
  -H "X-GitHub-Token: ghp_..." \
  -H "Content-Type: application/json" \
  -d '{
    "repo_url":"https://github.com/your-org/private-repo",
    "repo_name":"private",
    "callback_url":"https://node-backend.example.com/internal/ingest-complete"
  }'
```

When ingestion finishes, the AI agent POSTs:
```json
{
  "status": "complete",
  "repo_name": "private",
  "chunks_stored": 2760,
  "files_processed": 850,
  "commits_processed": 200,
  "branches_processed": 7,
  "total_chunks": 2760,
  "elapsed_seconds": 548
}
```

Or on failure:
```json
{ "status": "error", "repo_name": "private", "error": "git clone failed: ..." }
```

The Node side just exposes a normal HTTP handler:
```js
app.post('/internal/ingest-complete', (req, res) => {
  const { status, repo_name, error } = req.body;
  if (status === 'complete') notifyUser(repo_name, 'ready');
  else                       notifyUser(repo_name, `failed: ${error}`);
  res.sendStatus(200);
});
```

### 11c.5 Recommended timeouts at each layer

For a 30-minute ingestion, failures usually come from middle-layer timeouts.
Set these explicitly:

| Layer | Setting | Recommended |
|---|---|---|
| Browser fetch / Node fetch (ingest call) | timeout | **5 s** (the call returns instantly) |
| Browser fetch / Node fetch (`/api/query/*`) | timeout | **120 s** (query latency can spike) |
| SSE EventSource | n/a | persistent — no timeout |
| nginx `proxy_read_timeout` | (server) | **300 s** (already in DEPLOYMENT.md §5b) |
| AWS ALB idle timeout | (server) | **600 s** (raise from 60 s default) |
| Status-polling client | between requests | **5 s** for embedding phase, **15 s** otherwise |

Crucially: **never wrap the ingestion call itself in a `Promise.race(...timeout)`** — it returns in ~50 ms, then the long work happens in the background. Time it out only if you stop seeing status updates for 5+ minutes.

### 11c.6 Performance tuning knobs

In `.env`:
```dotenv
EMBED_BATCH_SIZE=16          # texts per Ollama /api/embed call. 8-32 are sweet spots.
EMBED_WORKERS=8              # concurrent batched calls. ↑ to 16 on bigger boxes.
EMBED_PROGRESS_EVERY=50      # status update every N chunks
MAX_COMMITS=200              # ↓ for faster ingestion of huge repos
RAG_TOP_K=5                  # ↓ for faster queries (default 5 is good)
USE_DUAL_MODEL=false         # leave OFF — only enable for deep reasoning
LLM_CONTEXT_WINDOW=4096      # ↓ to 2048 for faster but lower-quality responses
```

Restart after changes: `./deploy.sh restart`.

Measured ingestion throughput (nomic-embed-text on a t3.large):

| Repo | Sequential (legacy) | Parallel single-text (4 workers) | **Batched + parallel (16 × 8)** |
|---|---|---|---|
| ~500 chunks | ~100 s | ~28 s | **~6 s** |
| ~2 500 chunks | ~480 s | ~130 s | **~30 s** |
| ~10 000 chunks | ~32 min | ~9 min | **~2 min** |
| ~22 700 chunks (n8n-master-workflows) | ~75 min | ~22 min | **~5 min** |

The big win comes from `/api/embed` returning many vectors per HTTP call —
amortising network overhead. If your Ollama is too old to expose `/api/embed`
(404 on first probe), the embedder logs a warning and falls back to per-text
calls automatically. Upgrade Ollama (`brew upgrade ollama`) to recover the
speedup — needs Ollama ≥ 0.2.0.

---

## 12. Non-Functional Requirements

### 12a. Performance — query latency under 5 s

```bash
# Run 10 single-model queries; report timings
for i in $(seq 1 10); do
  T=$(curl -s -o /dev/null -w '%{time_total}' -X POST $INFINIUM_HOST/api/query \
    -H "Content-Type: application/json" \
    -d '{"query":"How is auth handled?","repo_name":"smoke-test"}')
  echo "Query $i: ${T}s"
done | tee /tmp/latency.log
awk '{sum+=$3; n++} END {print "Avg:", sum/n}' /tmp/latency.log
```
**Pass:** Average < 5.0 s.

### 12b. Security — JWT enforcement

```bash
# Every "auth-required" endpoint should 401 without JWT:
for path in /api/auth/me /api/dashboard /api/dashboard/stats; do
  CODE=$(curl -s -o /dev/null -w '%{http_code}' $INFINIUM_HOST$path)
  echo "$path → $CODE"
done
# All should be 401.
```

```bash
# HTTPS enforced (production only):
curl -i http://api.infinium.io/api/health
# Should redirect (301) to https://...
```

### 12c. Reliability — retry logic

Embedding has automatic retry (`backend/ingestion/embedder.py:_ollama_embed`).
Test by ingesting while temporarily stopping Ollama for 2-3 s:
```bash
# In one terminal:
curl -X POST $INFINIUM_HOST/api/ingest/local \
  -H "Content-Type: application/json" \
  -d '{"folder_path":"...","repo_name":"reliability-test"}'

# In another, stop & restart ollama within 2s:
docker stop infinium-ollama-1 && sleep 2 && docker start infinium-ollama-1
```
**Pass:** ingestion eventually completes (some chunks retried).

### 12d. Maintainability — modular routers

Verify modular structure:
```bash
ls AI-Agent/backend/api/routes/
# → access.py  dashboard.py  docs.py  git_history.py  ide.py
#   ingest.py  query.py  remote.py  repos.py  slack.py
```
Each FR module is its own file. Adding a new FR = one new file + one
`app.include_router(...)` line in `api/main.py`.

### 12e. Usability — Swagger UI is auto-generated

```
$INFINIUM_HOST/docs        # Swagger
$INFINIUM_HOST/redoc       # ReDoc
$INFINIUM_HOST/openapi.json # Raw OpenAPI 3 spec
```

---

## 13. Full end-to-end smoke test (one command)

```bash
#!/bin/bash
set -e
H=${INFINIUM_HOST:-http://localhost:8000}

echo "1. Health"
curl -fs $H/api/health | jq -r .status

echo "2. Ingest local repo"
curl -fs -X POST $H/api/ingest/local \
  -H "Content-Type: application/json" \
  -d "{\"folder_path\":\"$PWD\",\"repo_name\":\"smoke\"}" > /dev/null

echo "3. Wait for completion"
for i in $(seq 1 60); do
  S=$(curl -fs $H/api/repos/smoke/status | jq -r .status)
  echo "   [$i] $S"
  [ "$S" = "complete" ] && break
  sleep 3
done

echo "4. Query"
curl -fs -X POST $H/api/query \
  -H "Content-Type: application/json" \
  -d '{"query":"What does this project do?","repo_name":"smoke"}' \
  | jq -r .answer | head -5

echo "5. Feedback"
curl -fs -X POST $H/api/query/feedback \
  -H "Content-Type: application/json" \
  -d '{"query":"x","answer":"y","rating":4,"repo_name":"smoke"}' > /dev/null

echo "6. Slack smoke"
curl -fs -X POST $H/api/slack/test \
  -H "Content-Type: application/json" \
  -d '{"query":"hello","repo_name":"smoke"}' | jq -r .slack_text | head -3

echo "7. IDE explain"
curl -fs -X POST $H/api/ide/explain \
  -H "Content-Type: application/json" \
  -d '{"selected_code":"def add(a,b):return a+b","language":"python"}' \
  | jq -r .answer | head -3

echo "8. Auto-doc summarize"
curl -fs -X POST $H/api/docs/summarize/smoke | jq -r .draft | head -5

echo "✅ All FR smoke tests passed"
```

Save as `smoke-test.sh`, `chmod +x`, run it.

---

## 14. Test traceability matrix

| FR | Section | Endpoint(s) | Auto-test command |
|---|---|---|---|
| FR-001 | §1 | `/api/auth/*` | `curl -H "Bearer $JWT" /api/auth/me` |
| FR-002 | §2 | `/api/access/private` | curl with private repo + JWT |
| FR-003 | §2 | `/api/access/public` | curl public repo, no auth |
| FR-004 | §3 | `/api/ingest/private`, `/api/ingest/public`, `/api/ingest/local` | smoke-test.sh §2-3 |
| FR-005 | §4 | `/api/webhook/github` | curl signed webhook |
| FR-006 | §5 | `/api/query`, `/api/query/all`, `/api/query/git` | smoke-test.sh §4 |
| FR-007 | §5 | same as FR-006 — verify `sources` + citations | inspect `.sources` |
| FR-008 | §6 | `/api/query/feedback`, `/api/query/feedback/stats` | smoke-test.sh §5 |
| FR-009 | §7 | `/api/ide/*` + VS Code extension | smoke-test.sh §7 + manual F5 |
| FR-010 | §8 | `/api/slack/{command,events,test}` | smoke-test.sh §6 |
| FR-011 | §9 | `/api/dashboard*` | curl with JWT |
| FR-012 | §10 | `/api/docs/{frequent,gaps,summarize}` | smoke-test.sh §8 |
| FR-013 | §11 | `/api/repos/*` | curl GET/DELETE/POST |
| Code search & rewrite | §11b | `/api/code/{find,callers,rewrite,explain,cross-repo}` | see §11b.1–11b.5 |
| Cross-branch reasoning | §11b | `/api/repos/{repo}/branches/diff`, `/api/query/cross-branch` | see §11b.6–11b.7 |

---

## 15. Issue triage cheat-sheet

| Symptom | Likely cause | Fix |
|---|---|---|
| `/api/health` reports `ollama: disconnected` | Ollama not running | `ollama serve` (local) or `docker compose restart ollama` |
| Ingest hangs at `embedding` | `nomic-embed-text` not pulled | `ollama pull nomic-embed-text` |
| 401 on `/api/ingest` (without X-GitHub-Token) | Need either header or JWT | login or supply token from Node backend |
| Empty `sources` in answer | Repo not yet `complete` or query unrelated | check `/api/repos/{repo}/status` |
| Slack signature 401 | Body mutated by proxy | Set `SLACK_SIGNING_SECRET=""` for local testing |
| VS Code extension `HTTP error` | `infinium.apiUrl` setting wrong | edit settings → `http://localhost:8000` or your domain |
| Dashboard returns no repos | Repos ingested before `ingested_by` field landed | Re-ingest while logged in OR all repos remain visible to all users (backwards-compat) |
| Latency consistently > 5s | Dual-model on by default | Set `USE_DUAL_MODEL=false` in `.env`, restart |
| Webhook silent | Signature mismatch | Re-check `GITHUB_WEBHOOK_SECRET` in both `.env` and the GitHub webhook config |

---

## 16. Sign-off checklist for FYP defense

- [ ] `/api/health` returns `healthy` with all components green
- [ ] OAuth flow completes end-to-end, JWT received
- [ ] Public repo ingest succeeds via `/api/ingest/url`
- [ ] Private repo ingest succeeds with X-GitHub-Token header
- [ ] Single-repo query returns answer + ≥ 1 source citation
- [ ] Cross-repo query returns sources from ≥ 2 repos
- [ ] Multi-turn conversation maintains context (Q2 understands "the default")
- [ ] Feedback POST persists and shows up in `/stats`
- [ ] VS Code extension renders an answer panel from `Explain selected code`
- [ ] Slack `/api/slack/test` returns formatted text
- [ ] `/api/dashboard` shows user's repos + stats with JWT
- [ ] `/api/docs/summarize/{repo}` returns Markdown FAQ
- [ ] `/api/repos/{repo}` DELETE removes both status + ChromaDB collection
- [ ] Average single-model query latency < 5 s (NFR Performance)
- [ ] All auth-required endpoints return 401 without JWT (NFR Security)
- [ ] OpenAPI spec accessible at `/docs` (NFR Maintainability/Usability)

When every box is ticked, the agent is **defense-ready**.
