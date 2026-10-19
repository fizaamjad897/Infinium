# Architecture

The web client calls the Express API, which delegates indexing and code questions to this Python agent. Repository loaders produce chunks; embedding and retrieval use configured Ollama/Chroma services; generation combines retrieved context with the requested model. Optional feedback, query history, Git history, Slack and IDE integrations have separate routes and persistence paths. The VS Code extension talks to the agent API, not directly to Supabase.

Source paths: AI-Agent/backend/ingestion, backend/rag, backend/api, backend/auth and vscode-extension/src. Historical benchmark/dataset and health-domain folders are research inputs, not dependencies of the core developer-assistant path. Do not claim their files are a reproduced evaluation.

Data boundaries: source/token entry → local caches/index → configured model service → generated answer/citations. Every boundary needs permissions and retention review. A returned citation does not establish that an answer is correct.
