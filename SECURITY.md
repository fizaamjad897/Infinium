# Security policy

## Reporting

Use [GitHub private reporting](https://github.com/fizaamjd/Infinium/security/advisories/new). Include the affected branch/commit, reproduction with synthetic data, impact and suggested mitigation. Do not post credentials, customer data, private repository contents, or real email in an issue. If a credential was exposed, revoke it through its provider; removing a file does not invalidate a token or erase earlier commits.

The maintained branch is `evaluation-1`; there is no separate security support promise for old phase branches or tags. Dependency updates and CI checks do not establish a completed security audit.

## Known boundaries

- Repository ingestion can clone source and invoke model/embedding services. Use repositories you have permission to process; private code may be sent to configured model providers.
- Local users/session and ingestion-state JSON are runtime data, not fixtures. Previously tracked copies were removed from the maintained branch; historical commits still require credential review/rotation.
- JWT and integration secrets require explicit configuration. Review access checks for all public, local-ingestion, Slack and IDE routes before external exposure.
- Local data files, Chroma collections, repository caches, logs and model endpoints need their own access controls. Offline chunking tests do not establish tenant isolation or retrieval/model quality.

Use dedicated development services and explicit secrets. Review provider permissions, public network exposure, data retention, and deployment access before using private data. No penetration test or production-readiness certification is claimed.
