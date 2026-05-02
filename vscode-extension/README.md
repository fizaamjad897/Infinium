# Infinium VS Code Extension

A thin VS Code client for the Infinium reasoning agent. Lets you:

- Ask natural-language questions about your indexed repos without leaving the IDE.
- Right-click any selection → "Infinium: Explain selected code" or "Suggest improvements".
- Pick which repo to scope to (or query all of them).

## Build & run from source

```bash
cd vscode-extension
npm install
npm run compile
```

Then in VS Code: `F5` opens an Extension Development Host with Infinium loaded.

## Settings

| Setting | Default | What it does |
|---|---|---|
| `infinium.apiUrl` | `http://localhost:8000` | Base URL of your Infinium backend |
| `infinium.repoName` | _(empty)_ | Default repo. Empty = query all indexed repos |
| `infinium.jwtToken` | _(empty)_ | JWT from `/api/auth/github` (only needed for private repos) |

## Commands

| Command | Endpoint hit |
|---|---|
| Infinium: Ask a question | `POST /api/ide/query` |
| Infinium: Explain selected code | `POST /api/ide/explain` |
| Infinium: Suggest improvements | `POST /api/ide/improve` |
| Infinium: Choose default repository | `GET  /api/ide/repos` |
