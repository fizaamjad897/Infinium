# Dependency health

Snapshot: 2026-10-19. npm audits contact the registry and report known dependency advisories; these are not an application penetration test. Full and production-only counts are shown separately. Compatible updates were applied without force upgrades.

| Component | Full audit | Production-only audit |
| --- | --- | --- |
| Infinium extension | 0 findings | 0 findings |

The Python requirements have broad version ranges and no reproducible environment lock. No complete Python dependency audit or full model/runtime installation was performed. Offline chunking checks intentionally use only the standard library. Review and lock an environment separately before production deployment.

Recheck with npm audit and npm audit --omit=dev in the relevant package directory. Do not use npm audit fix --force merely to obtain a zero count. Source-level access controls, secret storage, workflow permissions and data retention require their own review.
