# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Breaking: renamed `FlatIDSchema[T]` to `IDRef[T]`, the scalar FK
  reference type. `IDRef[T]` exposes scalar request/response JSON Schema and
  OpenAPI shapes while still accepting `{"id": ...}` input for compatibility.
  No deprecated `FlatIDSchema` alias is kept for this alpha API rename.
- Breaking: renamed CRUD operation override points from `on_*` to `handle_*`
  to make clear that these methods are the operation handlers, not passive
  event hooks. The mapping is `on_list` -> `handle_list`,
  `on_get` -> `handle_get`, `on_create` -> `handle_create`,
  `on_update` -> `handle_update`, and `on_delete` -> `handle_delete`.
  No deprecated `on_*` aliases are kept for this alpha API rename.

## [3.0.0] - 2026-05-02

First public release of FastAPI-Restly. The major version reflects the
project's history — two prior production versions, across three companies,
over nearly ten years of use — not a series of public breaking changes. See
`docs/about.md` for the longer history.

### Added

- Async and sync class-based REST views (`AsyncRestView`, `RestView`) with
  auto-generated CRUD endpoints (list / get / create / update / delete).
- Class-based view primitive (`View`) with `@route()`, `@get()`, `@post()`,
  `@put()`, `@patch()`, `@delete()` decorators and `@fr.include_view(app)`
  for registration. Subclassing works as expected: routes resolve through
  the subclass's method dictionary, so overrides are picked up.
- React Admin compatibility layer (`AsyncReactAdminView`, `ReactAdminView`)
  speaking the `ra-data-simple-rest` wire contract.
- Automatic Pydantic v2 schema generation from SQLAlchemy 2.0 models via
  `create_schema_from_model()`.
- `ReadOnly[T]` and `WriteOnly[T]` field markers to control which fields
  are excluded from create/update inputs or from responses.
- `IDSchema[T]` / `IDRef[T]` generic schemas plus
  `OmitReadOnlyMixin` / `PatchMixin` schema transforms.
- Two pluggable query parameter dialects:
  - **V1** (JSONAPI-style: `filter[name]=John`, `filter[id]=1,2,3`,
    `filter[age]=>=18`, `filter[deleted_at]=null`, `contains[name]=john`).
  - **V2** (standard HTTP: `name=John`, `age__gte=18`,
    `deleted_at__isnull=true`, `name__contains=john`). Operator suffixes
    are dispatched per column type — booleans get equality / `__ne` /
    `__isnull` only; orderable types get the full comparison family;
    string types additionally get `__contains`.
- Endpoint/hook separation: CRUD endpoints delegate to `on_list`,
  `on_get`, `on_create`, `on_update`, `on_delete`. Override the hook for
  business logic, the route method for full HTTP control.
- CRUD utility methods on every view (`make_new_object`, `update_object`,
  `save_object`, `delete_object`) plus matching free functions
  (`fr.make_new_object`, `fr.update_object`, `fr.save_object`,
  `fr.async_make_new_object`, `fr.async_update_object`,
  `fr.async_save_object`) for use from custom routes and services.
- SQLAlchemy 2.0 base classes and mixins: `DataclassBase`, `IDBase`,
  `IDStampsBase`, `IDMixin`, `TimestampsMixin`, plus the `Plain*` family
  for projects that don't want dataclass-style models.
- `get_one_or_create` / `async_get_one_or_create` model helpers.
- Single-call configuration via `fr.configure()` accepting async/sync
  URLs, engines, session makers, or custom session generators.
- Session dependencies (`AsyncSessionDep`, `SessionDep`) and global state
  accessor (`get_fr_globals`, `use_fr_globals`).
- Per-view pagination configuration: `default_limit` / `max_limit` (V1)
  and `default_page_size` / `max_page_size` (V2). Defaults are
  *unlimited* — endpoints return every matching row unless the client
  asks for pagination or the view sets a default.
- Pagination-metadata response envelope opt-in via
  `include_pagination_metadata = True` on a view.
- Pagination/sort bounds (`limit`, `offset`, `page`, `page_size`) are
  validated by the auto-generated query-parameter Pydantic schema, so
  out-of-range values come back as standard 422 responses with the
  FastAPI validation envelope.
- Repeated query parameters (V1 `filter[created_at]=>=...&filter[created_at]=<...`,
  V2 `name__contains=hi&name__contains=ho`) are preserved as multiple
  AND'd predicates instead of being collapsed to one value.
- SQLAlchemy `IntegrityError` (unique-constraint, FK, not-null,
  check-constraint violations) is translated to HTTP 409 Conflict with a
  clean JSON body. Opt out via
  `fr.configure(install_default_exception_handlers=False)` or by
  registering your own handler before calling `configure`.
- `fastapi_restly.testing` pytest fixtures and `RestlyTestClient` for
  savepoint-isolated tests with response-status assertions.
- Documentation site (Sphinx, pydata-sphinx-theme): Getting Started,
  User Guide, How-To guides (query modifiers, view inheritance, endpoint
  overrides, React Admin, typing), and API reference. Published to
  GitHub Pages alongside coverage reports.
- Continuous integration matrix on Python 3.10–3.14 with separate
  lint / typing / coverage / docs jobs and pre-commit hooks.
- Example projects under `example-projects/`: `shop` (e-commerce CRUD),
  `blog` (minimal single-resource), and `saas` (multi-tenant project
  management with auth, audit stamps, and custom action routes).

[3.0.0]: https://github.com/rjprins/fastapi-restly/releases/tag/v3.0.0
