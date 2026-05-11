# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
