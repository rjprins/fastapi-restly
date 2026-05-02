# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

### Changed

### Fixed

### Removed

## [0.1.0] - 2026-05-02

Initial public release of FastAPI-Restly.

### Added

- Initial public release on GitHub.
- Async and sync class-based REST views (`AsyncRestView`, `RestView`) with
  auto-generated CRUD endpoints (list / get / create / update / delete).
- Class-based view system with `@route()`, `@get()`, `@post()`, `@put()`,
  `@delete()` decorators and `@fr.include_view(app)` for registration.
- Automatic Pydantic v2 schema generation from SQLAlchemy 2.0 models via
  `create_schema_from_model()`.
- `ReadOnly[T]` and `WriteOnly[T]` field markers to control which fields are
  excluded from create/update inputs or from responses.
- Two pluggable query parameter systems:
  - V1 (JSONAPI-style: `filter[name]=John`)
  - V2 (standard HTTP: `name=John`) including `contains[]` / `__contains`
    operators and alias support.
- Endpoint/hook separation: CRUD endpoints delegate to `on_list`, `on_get`,
  `on_create`, `on_update`, `on_delete` for clean override of business logic.
- View inheritance with prefix concatenation and dependency wiring.
- SQLAlchemy 2.0 base classes and mixins: `Base`, `IDBase`, `IDStampsBase`,
  `TimestampsMixin`.
- Single-call configuration via `fr.configure()` (replaces earlier
  `setup_async_database_connection` / `setup_database_connection` /
  `fr.settings`).
- Session dependencies (`AsyncSessionDep`, `SessionDep`) and global state via
  `fr_globals`.
- Pytest fixtures and utilities for testing with savepoint-based isolation
  (`activate_savepoint_only_mode()`), plus `DingTestClient` that asserts
  response status codes.
- Experimental React Admin compatibility layer: typed React Admin view support
  and an example shop admin UI.
- Documentation site built with Sphinx and `pydata-sphinx-theme`, including
  Getting Started, User Guide, How-To guides (typing, React Admin, view
  inheritance, endpoint overrides), and API reference. Published to GitHub
  Pages alongside coverage reports.
- Continuous integration with a Python 3.11 - 3.14 test matrix (3.14 marked
  experimental), framework coverage reporting via Codecov (OIDC), and
  pre-commit hooks.
- Example projects (`shop`, `saas`) demonstrating real-world usage patterns.

[Unreleased]: https://github.com/rjprins/fastapi-restly/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/rjprins/fastapi-restly/releases/tag/v0.1.0
