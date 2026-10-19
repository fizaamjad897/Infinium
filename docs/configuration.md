# Configuration

Copy AI-Agent/.env.example to AI-Agent/.env and fill in your own development values. Runtime configuration is defined in AI-Agent/backend/config.py. Use a strong JWT_SECRET, an OAuth app callback matching your actual auth service, and explicit Ollama/model settings. The example embedding-model name must match EMBED_MODEL; changing embeddings requires compatible reindexing. GitHub/Slack secrets are optional unless their integrations are enabled.

Persistent paths: CHROMA_DB_PATH, REPO_CACHE_DIR and DATA_DIR. Keep them private, outside source control, and separate per environment. Cloud reasoning may send source context to an external provider. Do not use old checked-in session records or deploy keys. AI-Agent/run.py binds port 8000 and development reload; review its bind/reload settings for deployment.

The Express app also has its own GitHub OAuth and JWT configuration. Do not assume the agent callback and Express callback are identical or that session formats are interchangeable.
