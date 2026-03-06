# FastAPI-Restly 1.0 Release Checklist

Last updated: 2026-03-06

## Scope

This checklist tracks readiness for `1.0.0-rc1` and final `1.0.0`.

## Release Criteria

- [x] Framework test suite passes
- [x] Example suites pass (`shop`, `blog`, `saas`)
- [x] API reference for public endpoints is published
- [x] Getting Started documentation is published
- [x] How-To guides for core features are published
- [x] CI workflow exists with Python 3.10-3.13 matrix
- [ ] Changelog for 1.0 finalized
- [ ] Version bump and release tagging finalized (`1.0.0-rc1` / `1.0.0`)
- [ ] PyPI release flow executed

## RC Verification Log (2026-03-06)

| Check | Command | Result |
|---|---|---|
| Framework tests | `make test-framework` | Pass |
| Example tests | `make test-examples` | Pass |
| Full test gate | `make test-all` | Pass |
| Documentation build | `uv run sphinx-build -M html docs site` | Pass |
| CI matrix definition | `.github/workflows/ci.yml` | Present (`3.10`, `3.11`, `3.12`, `3.13`) |

## Release Candidate Plan

1. Freeze feature work.
2. Prepare and review `CHANGELOG` for 1.0.
3. Set release version to `1.0.0-rc1`.
4. Run checklist commands on clean branch.
5. Tag and publish RC.
6. Run smoke period and collect regressions.

## GA Plan

1. Close RC regressions.
2. Set version to `1.0.0`.
3. Tag and publish GA release.
4. Announce with migration notes (if any).
