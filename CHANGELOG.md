# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Per-view pagination configuration via class attributes on `BaseRestView`:
  `default_limit` / `max_limit` for V1 and `default_page_size` /
  `max_page_size` for V2. The corresponding module-level defaults
  (`DEFAULT_LIMIT=100`, `MAX_LIMIT=1000`, `DEFAULT_PAGE_SIZE=25`,
  `MAX_PAGE_SIZE=1000`) are exported from `fastapi_restly` and
  `fastapi_restly.query`.
- `create_query_param_schema` / `create_query_param_schema_v1` /
  `create_query_param_schema_v2` now accept `default_limit` / `max_limit`
  (V1) and `default_page_size` / `max_page_size` (V2) keyword arguments so
  callers can override the bounds.

### Changed

- SQLAlchemy `IntegrityError` (unique-constraint, FK, not-null,
  check-constraint violations) is now translated to HTTP 409 Conflict with a
  clean JSON body instead of bubbling as a 500 Internal Server Error. Opt
  out via `fr.configure(install_default_exception_handlers=False)` or by
  registering your own handler for `IntegrityError` before calling
  `configure`. **Breaking** for clients that depended on a 500 response in
  these scenarios.
- **Breaking:** Pagination validation moved into the auto-generated
  query-parameter Pydantic schema. `limit` / `offset` (V1) and `page` /
  `page_size` (V2) are now constrained with `Field(ge=..., le=...)` and
  validated by FastAPI's standard Query layer. Out-of-range values now
  return a consistent **422** with the standard FastAPI validation
  envelope where the framework previously returned **400** with a single
  `detail` string. This affects: V1 `limit < 0` / `offset < 0` /
  `limit > MAX_LIMIT`, V2 `page < 1` / `page_size < 1` /
  `page_size > MAX_PAGE_SIZE`, and V1 non-integer `limit` (already 422
  via Pydantic's int parsing — now consistently 422 across all bad
  pagination inputs).
- **Breaking:** `apply_query_modifiers` (and the V1 / V2 implementations)
  now accept the validated query-parameter Pydantic model instance as
  their first argument instead of a raw `QueryParams`. Custom modifiers
  built on top of these helpers continue to work because raw
  `QueryParams` is still accepted as a fallback, but new code should pass
  the validated model so pagination bounds are enforced at the FastAPI
  boundary.
- View subclasses with a filter column literally named `limit` / `offset`
  / `sort` (V1) or `page` / `page_size` / `order_by` (V2) now skip that
  filter (emitting a `UserWarning` at schema-creation time) instead of
  silently shadowing pagination. Use a Pydantic alias to expose the
  column under a different name.
- The default `page_size` for V2 list endpoints is now **25** (was 100).
  Override per-view via `default_page_size` if the previous default is
  important to you.

### Fixed

- V2 `page=0` and `page_size=0` no longer slip past validation as falsy
  values: previously `_get_int_v2(...) or 1` silently coerced zero to one
  in the SQL while the metadata payload echoed the user's zero and
  produced a negative `offset`. Both inputs now return a 422 with the
  field name in the validation error.
- V1 negative `limit` / `offset` and V1 non-integer `limit` are now
  rejected with the same 422 error format. Previously the two paths
  produced different error envelopes (manual `HTTPException(400)` vs.
  Pydantic's 422).

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
