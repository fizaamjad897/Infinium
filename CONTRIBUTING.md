# Contributing

Start with the [README](README.md) and [architecture](docs/architecture.md). Use a small branch and keep changes specific to this repository's role.

## Development checks

```sh
python3 -m unittest discover -s tests -v
npm ci --prefix vscode-extension
npm run compile --prefix vscode-extension
python3 scripts/check-docs.py
```

Use disposable development accounts and local data. Never run ingestion, cloud model requests, OAuth callbacks, inbox processing, or seeding against production as a test. Offline tests load only the standard-library chunker, not API/config/model clients.

## Pull requests

Explain the problem, observable behavior, validation performed, and any configuration or migration impact. Add focused regressions for changed behavior; screenshots help for UI changes. Preserve existing contributors and third-party notices. Avoid committing generated builds, tokens, private messages, or local runtime state.

Report vulnerabilities through [private security reporting](https://github.com/fizaamjd/Infinium/security/advisories/new), not a public issue. General questions and reproducible bugs belong in issues. Maintainer review does not imply a response-time commitment.
