# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Reworks the class-based view API around a three-tier "handle" design. This is a
breaking change; views written for 0.5.x need updating.

### Changed (breaking)

- Each CRUD verb is now three tiers: a route shell (`get_many_endpoint`,
  `get_one_endpoint`, `create_endpoint`, `update_endpoint`, `delete_endpoint`)
  that owns the wire shape; a request handler (`handle_get_many`,
  `handle_get_one`, `handle_create`, `handle_update`, `handle_delete`) that runs
  `authorize` and the commit bracket; and a bare domain verb (`get_many`,
  `get_one`, `create`, `update`, `delete`) that is auth-free and commit-free —
  the usual override point. This replaces the `listing`/`get`/`create`/`update`/
  `delete` endpoints and the single `perform_*` tier.
- The commit now has a **single owner**. `handle_<verb>` runs `before_commit` →
  commit → `after_commit` around your domain logic, and the request-session
  dependency (`AsyncSessionDep` / `SessionDep`) **no longer commits on
  response**. `after_commit` therefore runs after the write is durable. A custom
  (non-CRUD) write route should reuse `handle_<verb>` or bracket its mutation
  with `with` / `async with self.write_action(action, ...)` to get the same
  lifecycle; a route that deliberately manages the lifecycle itself must commit
  explicitly with `_commit()` / `session.commit()`. Setting
  `commit_session_on_response=False`, or configuring a custom session generator,
  opts out of / takes over the commit.
- Renamed: `creation_schema`/`update_schema` → `schema_create`/`schema_update`;
  `build_from_schema`/`apply_schema` → `make_new_object`/`update_object`;
  `count_listing` → `count`. Response shaping goes through a single
  `to_response(obj_or_list, shape=ResponseShape.SINGLE)` method.
  `ViewRoute.LIST`/`GET` → `ViewRoute.GET_MANY`/`GET_ONE`.

### Added

- `authorize(action, obj, data)` override — an empty seam by default; raise
  `fr.Forbidden` / `fr.NotFound` to gate a verb (row visibility goes in
  `build_query`).
- `before_commit` / `after_commit` transaction hooks and a `snapshot()` helper
  for old-vs-new comparison.
- Typed request-time exceptions `NotFound`, `Forbidden`, `Conflict`, and
  `BadQueryParam`, subclassing `fastapi.HTTPException` (so a single
  `app.add_exception_handler(fr.NotFound, ...)` can reshape them).
- Top-level `make_new_object` / `update_object` / `save_object` /
  `delete_object` / `snapshot` helpers (and their `async_*` variants where
  applicable) for use outside a view.
- `write_action(action, *, obj, data)` — a context manager for custom write
  *actions* (`async with self.write_action("publish", obj=...): ...`) that runs
  the same authorize + commit bracket the CRUD handlers do. Plus the self-free
  `run_write_action` / `async_run_write_action` (in `fastapi_restly.views`)
  underneath, usable off the HTTP path. Create-shaped actions that omit `obj=`
  must assign the yielded handle's `.obj` before the block exits, otherwise a
  clean exit raises instead of committing with hooks unable to see the new
  object.
- `RestlyUncommittedChangesWarning` (default on; `warn_on_uncommitted=False` to
  disable) when a request finishes with uncommitted changes — the tell of a
  write route that forgot to commit.

### Fixed

- A `@route` method named like a bare verb (`create`/`update`/`delete`/
  `get_one`/`get_many`) is now rejected at registration: it shadowed the verb
  and collided with its `*_endpoint` route shell.
- Registering a View subclass alongside its parent on the same app no longer
  duplicates the child's routes.
- React Admin list/count/update paths now use the same `build_query`, `count`,
  authorization, and commit lifecycle as standard REST views; filter/sort
  resolution is limited to public schema fields.

## [0.5.1] - 2026-05-11

### Fixed

- Fixed `fr.include_view(...)` registration on `fastapi.APIRouter` parents.

## [0.5.0] - 2026-05-06

First public beta release.

### Added

- Class-based CRUD views for async and sync SQLAlchemy sessions with generated
  list, get, create, update, and delete routes.
- React Admin compatible `AsyncReactAdminView` and `ReactAdminView` variants
  for the `ra-data-simple-rest` wire contract.
- Generated schema support for read, create, and update payloads, including
  `ReadOnly`, `WriteOnly`, `IDSchema`, `IDRef`, and timestamp schema helpers.
- Standard list query support for filtering, sorting, pagination, relation
  aliases, and pagination metadata.
- Public `RestlyError` and `RestlyConfigurationError` exception hierarchy.
- Testing utilities through `RestlyTestClient`, savepoint-only mode helpers,
  and the `fastapi_restly.pytest_fixtures` pytest plugin.

### Changed

- Consolidated framework setup on `fr.configure(...)`, including async/sync
  engine configuration and response-session commit policy.
- Renamed built-in route methods to resource-oriented names:
  `list`, `get`, `create`, `update`, and `delete`.
- Renamed business-logic hooks to `perform_list`, `perform_get`,
  `perform_create`, `perform_update`, and `perform_delete`.
- Standardized schema component names on `ModelRead`, `ModelCreate`, and
  `ModelUpdate`.
- Made `sort` the canonical list ordering parameter.
- Split `__contains` and `__icontains` so case-sensitive and
  case-insensitive matching have distinct public operators.
- Exposed savepoint-only testing helpers through `fastapi_restly.testing`
  instead of the top-level package namespace.
- Renamed `build_listing_query` to `build_query` and broadened its role:
  `perform_get` now also routes through this hook, so a single override filters
  listing, the pagination total, and single-row fetches. A row hidden from
  listing returns 404 from `GET /{id}` too, and `perform_update` /
  `perform_delete` inherit the visibility check via `perform_get`.
- `perform_get` now issues a `SELECT ... WHERE pk = ?` instead of
  `session.get(...)`. Behaviour is unchanged for single-column primary keys;
  subclasses with composite primary keys must override `perform_get` themselves
  (a `NotImplementedError` is raised otherwise with a clear message).
- Advanced schema-to-object helpers now live in `fastapi_restly.objects`:
  `build_from_schema`, `apply_schema`, `save_object`, and `delete_object`,
  with async equivalents. The view methods use the same names for the mapping
  hooks and keep persistence at the `save_object` / `delete_object` boundary.

### Removed

- Removed the pre-stable `query=` argument from `perform_listing`. Override
  `build_query()` for SQL-level base query changes so listing, pagination
  totals, and single-row fetches stay aligned.

- Removed pre-release route and hook names such as `index`, `get`, `post`,
  `patch`, `delete`, `handle_list`, and `handle_get` from the public view API.
- Removed unsupported `get_one_or_create` helpers before the first stable
  release.
- Removed internal model helpers such as `TableNameMixin`, `underscore`, and
  `utc_now` from the public API surface.
- Removed duplicate pytest fixture exports from `fastapi_restly.testing`;
  the pytest plugin path is `fastapi_restly.pytest_fixtures`.

[Unreleased]: https://github.com/rjprins/fastapi-restly/compare/v0.5.1...HEAD
[0.5.1]: https://github.com/rjprins/fastapi-restly/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/rjprins/fastapi-restly/releases/tag/v0.5.0
