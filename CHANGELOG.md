# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Restly's declarative base mixes in SQLAlchemy's `AsyncAttrs`, so every model
  has `awaitable_attrs`: `await obj.awaitable_attrs.items` reads an unloaded
  attribute from plain async code, where a bare `obj.items` raises
  `MissingGreenlet`. Views eager-load what the response schema names, so this is
  for the code that runs outside that set — an `after_commit` hook, a custom
  business method.

- `get_relationship_loader_options()` is now a documented override point: it
  returns the `selectinload(...)` options derived from the response schema,
  applied on reads and on the write-response reload. Override it to eager-load
  relationships the schema does not name on both read and write paths. New
  how-to, "Relationship Loading and Async", collects the loading model and the
  `MissingGreenlet` fixes.

- A PostgreSQL CI leg now exercises the dialect-divergent surface SQLite hides:
  case-sensitive `contains` vs case-folding `icontains` (including non-ASCII),
  default `NULL` ordering, duplicate-sort-key pagination, real constraint
  violations classified into clean 409 details, and the psycopg cross-session
  fixture sharing.

### Changed

- The session fixtures (`restly_session` / `restly_async_session`) now isolate
  each test with SQLAlchemy's `create_savepoint` mode instead of patching
  `Session` / `AsyncSession`. `commit()` / `rollback()` behave as in production,
  and there is no shared identity map: a value added directly to the fixture
  session reaches a request only after a `flush()` or `commit()`.

- A session fixture with a generator but no matching sessionmaker now raises
  instead of skipping.

### Fixed

- Postgres 409 detail messages degraded to a generic fallback on psycopg 3. The
  `IntegrityError` classifier read `pgcode` (psycopg 2's attribute), but
  psycopg 3 — the driver Restly's PostgreSQL stack uses — exposes the SQLSTATE as
  `sqlstate`, so every constraint violation fell through to the raw driver text
  instead of a clean message like `Unique constraint violated: <constraint>`. It
  now reads `sqlstate` first, falling back to `pgcode`.

- A response schema embedding a relationship-backed field (`owner: OwnerRead`,
  `author: fr.IDRef[Author]`, or a list of either) worked on reads but 500'd on
  create and update with `MissingGreenlet` — for a row that had already
  committed. Reads eager-load those relationships from the response schema;
  writes flushed and refreshed with no loader options, and the refresh is what
  leaves a relationship unloaded, so the serializer hit a lazy load in the
  endpoint coroutine where SQLAlchemy's asyncio layer has no greenlet to suspend
  into. `save_object` now applies the same options reads do.

  The reload is skipped when everything the response schema names is already
  loaded, so a write whose response needs no relationship data costs no extra
  statement. It runs without `populate_existing`, so a relationship the caller
  had already populated keeps its value rather than being overwritten by a fresh
  read.

  Sync views never raised, but paid the same loads implicitly, one query at a
  time, during serialization. They now eager-load identically.

- The session fixtures isolate projects configured with a `session_generator` /
  `sync_session_generator` instead of dropping the request's write.

- `restly_async_session` no longer errors when a sync sessionmaker is also
  configured; it shares the sync fixture's connection.

- `fr.db.get_engine()` / `create_all()` and their async forms work inside the
  fixtures instead of silently no-opping.

## [0.8.0] - 2026-07-22

### Added

- `fr.MustExist[int, Post]` — an existence-checked scalar foreign key.
  `post_id: fr.MustExist[int, Post]` validates that the referenced row exists (a
  clean 404 on a miss, batched — no N+1) while the field stays a plain scalar
  everywhere: on the wire, in the column, in hooks (`data.post_id` is the id
  itself, not an `IDRef`/`IDSchema` wrapper), and to a type checker
  (`data.post_id` is `int`). The primary-key type comes first
  (`fr.MustExist[UUID, Account]` for a UUID key); when the column has a single
  `ForeignKey`, you can drop the model and let Restly infer it — `fr.MustExist[int]`.
  Reserve `IDRef` / `IDSchema` for relationship-named fields.

- The opt-in `fr.configure(warn_on_misuse=True)` lint now flags the
  `post_id: fr.IDRef[Post]` mistake — an `IDRef` / `IDSchema` reference typed on a
  scalar foreign-key **column**, where `data.post_id` becomes a wrapper instead of
  the plain id — steering to `fr.MustExist[int, Post]`. `IDRef` / `IDSchema` are
  now documented as relationship-named types, with `*_id` columns pointed at
  `MustExist`.

### Changed

- The tier vocabulary in docstrings and error messages now matches the docs:
  *endpoint method* (was "route shell"), *handler* (was "request handler"), and
  *business method* (was "business verb"). The misuse warning
  (`RestlyMisuseWarning`) and the bare-verb route-name `TypeError` use the new
  terms; match on `"endpoint method"` / `"business method"` if you asserted on
  those messages.

- `View.tags` is now typed `ClassVar[Iterable[str | Enum] | None]` instead of
  `ClassVar[Any]`, matching what FastAPI accepts for router tags. Runtime
  behaviour is unchanged; type checkers now flag a wrongly-typed `tags`.

- The `[testing]` extra now installs `httpx2` alongside `httpx`. Newer Starlette
  `TestClient` prefers `httpx2` and emits a `StarletteDeprecationWarning` when
  only `httpx` is present; shipping both keeps `RestlyTestClient` on the
  non-deprecated path without dropping support for the older Starlette versions
  in our range, which still import `httpx` directly.

- `RestlyTestClient` now prefers `httpx2` (falling back to `httpx`), matching
  Starlette's own `TestClient`. The "install the `[testing]` extra" hint that
  Restly raises when the test client is missing now also fires under newer
  Starlette, which signals the absence as `httpx2` (and via a `RuntimeError`)
  rather than a missing `httpx`.

### Removed

- The `orjson` dependency and the orjson serializer on URL-created engines.
  `JSON` columns now use SQLAlchemy's default encoder (the standard library
  `json`) on every engine: naive datetimes are no longer silently stamped as
  UTC, and datetime/UUID values or keys now raise `TypeError` at write time
  instead of being coerced to strings. To keep the old behavior, build your
  own engine with `json_serializer=`/`json_deserializer=` and pass it to
  `fr.configure()`.

### Fixed

- Filtering on a dotted path deeper than one relationship hop
  (`?city.country.code=NL`) no longer fails with a 500. Deep paths were
  advertised and resolved, but the filter clause collected its joins into an
  unordered set, so the second hop could be joined before the first — an
  implicit cartesian product that the database rejects as an ambiguous join.
  Filter joins now apply in path order, as sorting always did. Two shapes
  remain unsupported and are now documented as such: paths through a
  self-referential relationship, and two filter paths that reach the same
  table — those need per-path join aliasing.

- List views no longer advertise filter parameters that can never execute for
  collection-typed columns (`JSON`/`ARRAY`, i.e. fields typed `dict`,
  `list[...]`, `Sequence`, and similar). A query-string value cannot coerce
  into a collection, so `eq`/`__in`/`__ne` — and, for parametrized generics
  such as `list[str]`, the range family — answered 400 on every request;
  those parameters are now omitted from the generated schema and OpenAPI, and
  only `__isnull`, which works, is kept. On generated endpoints, requests
  using the removed parameters now fail validation as unknown parameters
  (422) instead of passing validation and failing at execution; callers
  passing raw `QueryParams` to `apply_list_params` still get the 400.
  `pydantic.Json[...]` fields keep their filters: their validation parses
  the query string into the collection, so they execute.

- Registering the same view class twice on the same app or router
  (`fr.include_view(app, V); fr.include_view(app, V)` — a double import, or the
  decorator form combined with an explicit call) no longer mounts its routes
  twice. The duplicate call is now a no-op: each parent tracks the view classes
  already mounted on it, and an app and its `.router` attribute count as one
  parent. The opt-in `fr.configure(warn_on_misuse=True)` lint flags the
  duplicate call with a `RestlyMisuseWarning`. Registering on *different*
  parents (a public and an admin app, `/v1` and `/v2` sub-apps) still mounts
  on each, as before.

- `fr.IDRef[T]` / `fr.IDSchema[T]` foreign-key fields now work under any column
  name, not only fields ending in `_id`. A field like
  `post_fk: fr.IDRef[Post]` backed by a non-`_id` FK column was silently
  misrouted — the resolved ORM object was assigned into the integer FK column
  and the request failed at flush (`sqlalchemy.exc.ProgrammingError`) instead of
  at validation. Reference routing (the create plan, the in-place update, and
  the both-supplied FK/relationship consistency check) now decides column vs.
  relationship and derives the partner attribute from the SQLAlchemy mapper
  rather than from the field name, so any FK column name resolves correctly.

- Creating with a relationship exposed as a reference field (e.g.
  `invoice: fr.IDRef[Invoice]`) no longer fails when the model's local FK column
  is a required constructor argument (no `init=False` and no default) and a
  reference to an existing row is supplied. The resolved row's id is now passed
  at construction instead of being assigned afterward, so the dataclass
  `__init__` no longer raises `TypeError: ... missing 1 required keyword-only
  argument`. Declaring the FK column `init=False` is still supported but no
  longer required.

- A null reference — an explicit `post=None` for a `post: fr.IDRef[Post] | None`
  field, or such a field omitted and defaulting to `None` — no longer raises
  `TypeError: __init__() missing 1 required keyword-only argument: 'post_id'`
  when the model's local FK column is a required constructor argument (no
  `init=False` and no default). A null reference now takes a dedicated plan
  path that writes the field's own slot and passes a partner kwarg (as
  `NULL`) at construction when the dataclass requires it — in either
  direction (FK column or relationship) and nothing more, so an unset sibling
  reference field (schemas may declare both names of an FK/relationship pair
  as reference fields) never clobbers the side the client supplied. A null
  reference to a *nullable* FK creates the row with a NULL FK; to a
  *non-nullable* FK it now fails at flush as a regular `IntegrityError` (the
  standard 409 path) instead of the 500 `TypeError`.

- `fr.IDRef[T]` now serializes through the type itself under plain Pydantic
  `from_attributes`, so a reference field validated outside a Restly route — a
  nested model, a custom endpoint, or `response_model=` on a raw schema — no
  longer crashes with a cryptic `int_type` error when the value is the related
  ORM row. Previously only the view layer's `to_response_schema` handled this,
  by pre-extracting the scalar id; that redundant special-casing is removed and
  both `IDRef[T]` and `IDSchema[T]` self-serialize from a related row or a raw
  scalar id along a single path. Response output is unchanged.

## [0.7.0] - 2026-06-11

### Added

- The generated route shells (`get_many_endpoint`, `create_endpoint`, ...)
  now carry one-line override-redirect docstrings, so `help(RestView)`, source
  readers, and coding agents see which tier to override (`<verb>` for domain
  logic, `handle_<verb>` for orchestration, `to_response` for shape). The
  docstrings are stripped from generated routes at registration so framework
  guidance never appears as OpenAPI operation descriptions in your API;
  endpoints you define or override yourself keep FastAPI's normal docstring
  behavior.

- Scalar `fr.IDRef[T]` foreign-key fields are now filterable on list endpoints
  by their own public name. Previously `post_id: fr.IDRef[Post]` — the FK form
  the tutorial teaches — generated no filter parameter at all, so
  `GET /comments/?post_id=1` returned a 422 that looked like client error; the
  only filterable form was a plain `post_id: int`. An `IDRef` id is treated as
  opaque, so it gets equality, `__in`, `__ne`, and `__isnull` (uniform across
  int/UUID/string primary keys) but not the range or substring operator
  families.
- Python 3.14 is now officially supported and tested. It was previously in the
  CI matrix as an experimental (allowed-to-fail) target while `orjson` lacked a
  3.14 wheel; that wheel now ships, the full test suite passes on 3.14, and the
  job gates CI like every other supported version.
- `fr.db.create_all(Base)` / `fr.db.async_create_all(Base)` — dev/demo helpers
  that create every table for a declarative base (or a `MetaData`) on the engine
  configured via `fr.configure()`, replacing the `engine =
  fr.db.get_async_engine(); async with engine.begin() as conn: await
  conn.run_sync(Base.metadata.create_all)` boilerplate in quickstarts and test
  setup. Use Alembic migrations in production.
- Opt-in registration-time misuse warnings: `fr.configure(warn_on_misuse=True)`
  makes `include_view` lint each registered view class and emit
  `fr.exc.RestlyMisuseWarning` for the three dominant misuse patterns —
  overriding a route shell (`<verb>_endpoint`) where a business-verb override
  was meant, calling `session.commit()` directly in a view method, and
  hand-rolling a CRUD route set on a bare `View` instead of subclassing
  `RestView` / `AsyncRestView`. Each message names the idiomatic fix. Off by
  default; intended for development, project templates, and CI.

### Changed

- The `RestlyUncommittedChangesWarning` message now leads with the fix
  (bracket the mutation with `write_action(...)` or reuse a `handle_<verb>`)
  and offers only the per-route suppression
  (`session.info["_fr_suppress_uncommitted"] = True`) for intentional
  dry runs. It no longer advertises the global
  `warn_on_uncommitted=False` opt-out, which readers took as a fix for the
  warning instead of committing their changes.
- **Breaking — top-level `fr.*` namespace curated.** Errors, HTTP exceptions, and
  the uncommitted-changes warning moved to `fr.exc` (`fr.exc.NotFound`,
  `fr.exc.RestlyError`, …) — the `exceptions` module is renamed `exc`, mirroring
  `sqlalchemy.exc`. Advanced helpers moved to their layer submodules: schema↔ORM
  helpers to `fr.objects.*` (`make_new_object` / `save_object` / `snapshot` / …
  and the `async_*` variants), list query helpers to `fr.query.*`
  (`create_list_params_schema`, `apply_list_params`), engine accessors to
  `fr.db.*` (`get_engine`, `get_async_engine`), and `IDMixin` to `fr.models.*`.
  The route decorators (`@fr.get` / `@fr.post` / … / `@fr.route`), views,
  registration, schemas, model bases, `configure`, the session
  helpers/dependencies, and the view support types (`Action` / `ViewRoute` /
  `ResponseShape` / `ListingResult`) stay top-level. **Migration:** e.g.
  `fr.NotFound` → `fr.exc.NotFound`,
  `fr.make_new_object` → `fr.objects.make_new_object`,
  `fr.get_async_engine` → `fr.db.get_async_engine`.

### Fixed

- `fr.open_session()` / `fr.open_async_session()` now resolve the same session
  source as `SessionDep` / `AsyncSessionDep`: a custom session generator passed
  to `fr.configure(session_generator=...)` / `sync_session_generator=...` takes
  precedence over the built-in factory. Previously the context managers always
  used the built-in factory, so a generator-only configuration worked inside
  request handlers but raised `RestlyConfigurationError` off-HTTP (in scripts,
  background jobs, or a custom dependency wrapping `open_*session()`). The two
  session entry points are now consistent.

## [0.6.1] - 2026-06-02

### Changed

- Reference resolution (`IDRef`/`IDSchema`) no longer mutates the validated
  request model in place. The resolver now returns a `{field: resolved}` mapping
  that the write path consumes, so the request model keeps its wire shape (its
  reference fields stay `IDRef[T]` values rather than being overwritten with ORM
  rows). Behavior of create/update is unchanged; the internal helpers
  `build_create_plan` / `apply_update_to_object` /
  `validate_resolved_reference_consistency` gained a `resolved` argument.
- The `standard` extra is now runtime-only and mirrors `fastapi[standard]`. It no
  longer pulls the test toolchain (`pytest`, `pytest-asyncio`, `pytest-cov`,
  `httpx`) or bundles the `aiosqlite` driver, so installing
  `fastapi-restly[standard]` for production no longer drags pytest into the image.
  Test tooling stays in the `testing` extra; the database driver is now an
  explicit choice (Restly remains driver-agnostic). **Migration:** if you relied
  on `[standard]` for test dependencies, switch to `[testing]`; if you ran on
  SQLite, add `aiosqlite` to your dependencies directly.
- The `testing` extra now includes only the third-party packages Restly's shipped
  test helpers import — `pytest`, `pytest-asyncio`, and `httpx` (for
  `RestlyTestClient` and the `restly_*` fixtures). `pytest-cov` is no longer
  pulled in; add it yourself if you want coverage reports.
- Removed the `docs` extra from the published extras. It only installed the
  toolchain for building Restly's own documentation site (a maintainer concern);
  those dependencies remain in the `dev` dependency group. The published extras
  are now `standard` and `testing`.

### Fixed

- `RestlyUncommittedChangesWarning` no longer false-positives on every write run
  under the savepoint test fixtures: the patched `commit` clears the
  pending-changes flag (mimicking the real `after_commit`), while a genuinely
  forgotten commit still warns.
- An `IDRef` list field that references the same id more than once no longer
  raises a confusing `Id not found: set()` 404 when the referenced rows all
  exist; a genuinely missing id is now named in the error.
- An `IDRef` list field now resolves in the client-sent order instead of
  silently reordering to the database's primary-key order (duplicate ids are
  collapsed, first occurrence wins).
- A `ReadOnly` or `WriteOnly` marker nested inside a field's type instead of
  wrapping it (such as `Optional[WriteOnly[str]]`, `WriteOnly[str] | None`, or
  `list[WriteOnly[str]]`) is now rejected with a `RestlyConfigurationError`
  instead of silently no-op'ing. Nested there the marker has no effect — a
  `WriteOnly` field would leak into responses and a `ReadOnly` field would stay
  writable — so the framework now raises when the schema is defined (and again at
  view registration for schemas that do not derive from `BaseSchema`), pointing
  to the safe `Marker[Optional[T]]` form.
- A list view no longer advertises filter query parameters for fields that are
  not filterable columns — a to-many relationship (`books: list[BookRef]`) or a
  reference field that does not resolve to a column. These appeared in OpenAPI
  but always returned 400. Filter-param generation now validates each field
  against the model with the same column-resolution predicate the request path
  uses, so non-column fields no longer get filter params; to-one dotted traversal
  is unchanged. (`create_list_params_schema` now takes the queried `model` as a
  required argument.)

## [0.6.0] - 2026-06-01

Reworks the class-based view API around a three-tier "handle" design. This is a
breaking change; views written for 0.5.x need updating.

### Changed (breaking)

- Each CRUD verb now has three tiers: route shell (`*_endpoint`), request
  handler (`handle_*`), and domain verb (`get_many`, `get_one`, `create`,
  `update`, `delete`). This replaces the `listing`/`get`/`create`/`update`/
  `delete` endpoints and the single `perform_*` tier.
- The framework owns commits. `handle_<verb>` and `write_action` run
  `before_commit` → commit → `after_commit`; request-session dependencies no
  longer commit on response. Custom write routes should reuse `handle_<verb>` or
  bracket mutations with `self.write_action(...)`. Manual `session.commit()` is
  only for shapes the bracket does not model, such as batch commits.
  `commit_session_on_response` is removed; custom session generators construct
  and clean up sessions but do not own commits.
- Renamed: `creation_schema`/`update_schema` → `schema_create`/`schema_update`;
  `build_from_schema`/`apply_schema` → `make_new_object`/`update_object`;
  `count_listing` → `count`. Response shaping goes through a single
  `to_response(obj_or_list, shape=ResponseShape.SINGLE)` method.
  `ViewRoute.LIST`/`GET` → `ViewRoute.GET_MANY`/`GET_ONE`.

### Added

- `authorize(action, obj, data)` override — an empty override by default; raise
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
  actions. It shares the CRUD authorize + commit bracket. Create-shaped actions
  that omit `obj=` must assign the yielded handle's `.obj` before exit.
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
- A paginated list sorted on a non-unique column now appends the primary key as
  a final `ORDER BY` tiebreaker, so rows are no longer skipped or repeated across
  pages. Applies to both the standard and React Admin sort paths.
- A `build_query` that joins a to-many relationship no longer fans out: `get_many`
  de-duplicates entities and the list total counts distinct rows, so the page and
  `total_count` agree. A no-op for queries without such a join.
- A `WriteOnly` field no longer leaks into a response, including from a nested
  response schema. `WriteOnly` fields are now excluded from serialization at the
  field level (`exclude=True` on the marker), so they are stripped recursively on
  the wire and dropped from the OpenAPI response schema, while staying required,
  documented request inputs. Prefer `WriteOnly[Optional[T]]` over
  `Optional[WriteOnly[T]]` — a `WriteOnly` marker buried only inside a union is
  not excluded.

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
- Made `sort` the standard list ordering parameter.
- Split `__contains` and `__icontains` so case-sensitive and
  case-insensitive matching have distinct public operators.
- Exposed savepoint-only testing helpers through `fastapi_restly.testing`
  instead of the top-level package namespace.
- Renamed `build_listing_query` to `build_query` and broadened its role:
  `perform_get` now also routes through this hook, so a single override filters
  listing, the pagination total, and single-row fetches. A row hidden from
  listing returns 404 from `GET /{id}` too, and `perform_update` /
  `perform_delete` inherit the visibility check via `perform_get`.
- `perform_get` now issues `SELECT ... WHERE pk = ?` instead of
  `session.get(...)`. Single-column primary-key behavior is unchanged.
  Composite-primary-key subclasses must override `perform_get`.
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

[Unreleased]: https://github.com/rjprins/fastapi-restly/compare/v0.8.0...HEAD
[0.8.0]: https://github.com/rjprins/fastapi-restly/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/rjprins/fastapi-restly/compare/v0.6.1...v0.7.0
[0.6.1]: https://github.com/rjprins/fastapi-restly/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/rjprins/fastapi-restly/compare/v0.5.1...v0.6.0
[0.5.1]: https://github.com/rjprins/fastapi-restly/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/rjprins/fastapi-restly/releases/tag/v0.5.0
