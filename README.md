# Infinium

![CI](https://github.com/fizaamjd/Infinium/actions/workflows/ci.yml/badge.svg?branch=evaluation-1)

Infinium is a repository-aware developer assistant. Its Python service indexes code, retrieves relevant chunks and uses configured Ollama models to answer codebase questions. This repository also includes a VS Code extension and historical research material.

The maintained application lives on **evaluation-1**, now the default branch. The older main and phase branches are retained for history; their contents are not interchangeable releases.

## Live demo video

https://github.com/user-attachments/assets/5620ed13-da0e-47b4-8b99-5827b910646f

[Download video](docs/media/infinium-demo.mp4) · [HTML presentation source](docs/media/infinium-demo.html)

A motion walkthrough of the complete Infinium product. Illustrative product states are not recorded model-quality benchmarks. The web client and application API are maintained in companion repositories.

## Project structure

| Path | Purpose |
| --- | --- |
| AI-Agent/backend/ingestion | Repository loading, chunking and embeddings |
| AI-Agent/backend/rag | Retrieval, context, reasoning and generation |
| AI-Agent/backend/api | FastAPI routes and request models |
| AI-Agent/backend/auth | GitHub OAuth and session handling |
| vscode-extension/ | Ask, explain, improve and repository-selection commands |
| HumanEval-XL/, polyglot/, research folders | Historical evaluation/input material; not a verified scorecard |

## Run locally

Use Python 3.11 and Node.js 22 for the maintained checks. A running Ollama instance and its configured models are required for the full agent; cloud models also need provider access.

```sh
cd AI-Agent
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
# Configure your own development OAuth app, secrets and model endpoints.
python run.py
```

API documentation: http://localhost:8000/docs. Startup can initialize local stores and model clients; use disposable paths and repositories. Do not run provisioning/deployment scripts against production as a smoke test. The example env and settings must agree on embedding/reasoning model names; see [configuration](docs/configuration.md).

For the extension, run npm ci and npm run compile inside vscode-extension, then open that folder in VS Code and press F5. Configure infinium.apiUrl and an optional repository/session through extension settings. This is an extension-development workflow, not a claim of Marketplace publication.

## Verification and limitations

Offline tests cover function-aware chunk boundaries with no models or network. CI also compiles the extension and checks documentation links. It does not ingest real repositories, measure answer accuracy, run HumanEval, or validate OAuth/Slack/IDE endpoints. No benchmark score is asserted without a reproducible dataset, model/configuration, run logs and evaluation protocol.

Private repository processing and refactor suggestions require human review and appropriate rights. See [testing](docs/testing.md), [security](SECURITY.md), and [architecture](docs/architecture.md).

## Companion repositories

| Repository | Responsibility |
| --- | --- |
| [Infinium](https://github.com/fizaamjd/Infinium) | Python RAG agent, ingestion, retrieval, integrations and VS Code extension |
| [Infinium-BE](https://github.com/fizaamjd/Infinium-BE) | Express application API, Supabase persistence, auth and service orchestration |
| [Infinium-FE](https://github.com/fizaamjd/Infinium-FE) | Next.js user interface |

## Maintenance status

See [verification](docs/verification.md) for checks actually run and [dependency health](docs/dependency-health.md) for remaining findings.

## Contributing and maintenance

[Contributing](CONTRIBUTING.md) · [Support](SUPPORT.md) · [Security](SECURITY.md) · [Code of conduct](CODE_OF_CONDUCT.md) · [Changelog](CHANGELOG.md)

## License

[MIT](LICENSE) for original contributions the maintainer is authorized to license. Third-party and collaborator rights remain separate; see [license scope](docs/license-scope.md).
