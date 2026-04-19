# Infinium — Autonomous Reasoning Agent

> Ask natural-language questions about your entire codebase. Powered by RAG + Ollama cloud models.

---

## Quick Start (Local Dev)

### 1. Install Ollama

```bash
# macOS
brew install ollama
ollama serve   # start Ollama server in background
```

### 2. Pull Required Models

```bash
# Embedding model (runs locally, fast)
ollama pull nomic-embed-text

# Cloud reasoning models (require Ollama account at ollama.com)
ollama run minimax-m2.7:cloud
ollama run kimi-k2.5:cloud
```

> 💡 **Cloud models** (`minimax-m2.7:cloud`, `kimi-k2.5:cloud`) route through Ollama's cloud API — no GPU needed, very fast. Requires a free account at [ollama.com](https://ollama.com).

### 3. Set Up Environment

```bash
cd AI-Agent
# .env is already configured — just verify it exists:
cat .env
```

### 4. Install Dependencies & Run

```bash
cd AI-Agent
pip install -r requirements.txt

# Verify all models are ready
python init_models.py

# Start the API server
python run.py
```

API docs available at: **http://localhost:8000/docs**

---

## Docker Deploy (Recommended for Production)

```bash
cd AI-Agent

# Copy and configure environment
cp .env.example .env
# Edit .env with your GitHub OAuth credentials

# Build and start all services
docker-compose up --build -d

# Pull models into the Ollama container
docker exec infinium-ollama-1 ollama pull nomic-embed-text
docker exec infinium-ollama-1 ollama run minimax-m2.7:cloud
docker exec infinium-ollama-1 ollama run kimi-k2.5:cloud

# Check health
curl http://localhost:8000/api/health
```

---

## API Usage

### Ingest a Repository

```bash
# From GitHub URL (requires auth token)
curl -X POST http://localhost:8000/api/ingest \
  -H "Authorization: Bearer <your_jwt>" \
  -H "Content-Type: application/json" \
  -d '{"repo_url": "https://github.com/org/repo", "repo_name": "my-repo"}'

# From local folder (no auth needed — for dev/testing)
curl -X POST http://localhost:8000/api/ingest/local \
  -H "Content-Type: application/json" \
  -d '{"folder_path": "/path/to/repo", "repo_name": "my-repo"}'
```

### Query Your Codebase

```bash
# Single repo query (fast, ~2-5s)
curl -X POST http://localhost:8000/api/query \
  -H "Content-Type: application/json" \
  -d '{"query": "Why was JWT deprecated?", "repo_name": "my-repo"}'

# Query ALL repos (cross-codebase reasoning)
curl -X POST http://localhost:8000/api/query/all \
  -H "Content-Type: application/json" \
  -d '{"query": "How is authentication handled across our services?"}'

# Deep reasoning mode (slower but thorough — uses both kimi + minimax)
curl -X POST http://localhost:8000/api/query \
  -H "Content-Type: application/json" \
  -d '{"query": "Why did we move to microservices?", "repo_name": "my-repo", "use_dual_model": true}'
```

### GitHub OAuth Login

```
GET http://localhost:8000/api/auth/github
→ Redirects to GitHub OAuth
→ On success: returns JWT token
```

---

## Speed Tuning

| Setting | Default | Effect |
|---|---|---|
| `USE_DUAL_MODEL` | `false` | Single model: ~2-5s. Dual model: ~8-15s |
| `RAG_TOP_K` | `5` | Fewer chunks = smaller prompt = faster LLM |
| `LLM_CONTEXT_WINDOW` | `4096` | Larger window = slower cloud model |
| `use_expansion` | `false` | Query expansion adds 1 extra LLM call |

To enable deep reasoning for a specific query, pass `"use_dual_model": true` in the request body. The server default stays fast.

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `OLLAMA_BASE_URL` | Yes | Ollama server URL (default: `http://localhost:11434`) |
| `LLM_MODEL` | Yes | Synthesis model (default: `minimax-m2.7:cloud`) |
| `CODE_REASONING_MODEL` | Yes | Reasoning model (default: `kimi-k2.5:cloud`) |
| `EMBED_MODEL` | Yes | Embedding model (default: `nomic-embed-text`) |
| `GITHUB_CLIENT_ID` | OAuth only | GitHub OAuth App client ID |
| `GITHUB_CLIENT_SECRET` | OAuth only | GitHub OAuth App client secret |
| `JWT_SECRET` | Yes | Random secret for JWT signing — change in production! |
| `USE_DUAL_MODEL` | No | Enable dual-model by default (`false` = fast mode) |

---

## Architecture

```
User Query
    ↓
FastAPI Backend (/api/query)
    ↓
Hybrid Retriever (ChromaDB semantic + BM25 keyword)
    ↓
Context Chunks (top 5 most relevant)
    ↓
[Optional] kimi-k2.5:cloud — code reasoning step
    ↓
minimax-m2.7:cloud — answer synthesis with citations
    ↓
Response + Sources + Reasoning Trace
```

---

## Project Structure

```
AI-Agent/
├── backend/
│   ├── api/          — FastAPI routes (query, ingest, repos, auth)
│   ├── rag/          — RAG pipeline (retriever, generator, conversation)
│   ├── ingestion/    — GitHub loader, chunker, embedder
│   ├── auth/         — GitHub OAuth + JWT handler
│   └── feedback/     — Feedback store
├── config.py         — Centralized settings (pydantic-settings)
├── init_models.py    — Pre-flight model verification
├── run.py            — Local dev server
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```
