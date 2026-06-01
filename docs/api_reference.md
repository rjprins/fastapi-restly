# API Reference

This page documents:
- Generated HTTP endpoints and query behavior.
- Key public symbols with brief descriptions.
- Full Python API reference generated via Sphinx autodoc.

## Generated REST Endpoints

Register a view with `fr.include_view(app, ViewClass)` or `@fr.include_view(app)`. `fr.AsyncRestView` and `fr.RestView` expose the same generated resource surface:

| Method | Path | Purpose | Default Status |
|---|---|---|---|
| `GET` | `/{prefix}/` | List resources | `200` |
| `POST` | `/{prefix}/` | Create resource | `201` |
| `GET` | `/{prefix}/{id}` | Get resource by ID | `200` |
| `PATCH` | `/{prefix}/{id}` | Partial update | `200` |
| `DELETE` | `/{prefix}/{id}` | Delete resource | `204` |

Notes:
- Updates use `PATCH`, not `PUT`. React Admin views also expose `PUT /{id}` for `ra-data-simple-rest`; see [React Admin Integration](howto_react_admin.md).
- `GET /{id}` and `DELETE /{id}` return `404` when the object is not found.
- Read-only schema fields are ignored on create/update.
- `*_id: IDRef[Model]` inputs are resolved to SQLAlchemy objects and validated. The scalar id is the related primary-key type, such as `int` or `UUID`.

## Query Parameters (List Endpoint)

`GET /{prefix}/` exposes **list parameters** derived from the response schema. Parameter keys use public field names, including aliases and dotted relation paths.

- Filtering: `?name=John&created_at__gte=2024-01-01`
  - Suffixes: `__in`, `__gte`, `__lte`, `__gt`, `__lt`, `__ne`, `__isnull`, `__contains`, `__icontains` (contains operators are string fields only)
  - OR-values (IN): `?id=1,2,3` (comma-separated values are OR-combined for `eq`)
  - Explicit IN: `?status__in=active,pending`
  - NOT-IN: `?status__ne=archived,deleted` (comma-separated values are AND-combined for `__ne`)
  - Aliased fields use only the alias as the URL key; the Python field name is not accepted.
- Contains: `?name__contains=John` (case-sensitive where the SQL backend supports that distinction)
- IContains: `?name__icontains=john` (case-insensitive)
  - Repeat the parameter to AND multiple terms — this is the precise form: `?name__contains=john&name__contains=doe`.
  - As a convenience, whitespace inside one value is also AND-split: `?name__contains=john%20doe` is equivalent.
  - `%`, `_`, and `\\` are escaped before building the SQL `LIKE` / `ILIKE`.
- Sorting: `?sort=name,-created_at`
- Pagination: `?page=2&page_size=10`
  - **Opt-in.** Omitting `page_size` returns every matching row (no implicit cap).
  - For public/production endpoints, set `default_page_size` and `max_page_size` explicitly on the view class.

**Unknown query keys are rejected.** Generated list endpoints validate the query string against schema-derived parameters. Typos, Python names for aliased fields, and unsupported operators return 422 instead of widening the result set. To allow extra view-specific keys, declare them on the view class:

```python
class UserView(fr.AsyncRestView):
    extra_query_params = ("include_deleted",)
```

Relation filtering uses dot notation, and aliases apply to every path segment. If `author` is aliased to `writer` and `name` to `authorName`, the URL key is `writer.authorName`.

### Low-level helpers

`fr.create_list_params_schema(...)` and `fr.apply_list_params(...)` power generated list endpoints. Use the view classes for normal CRUD. Call these helpers directly only for custom endpoints that need the same list grammar. Pass a validated params-schema instance, not raw `QueryParams`.

## Optional Pagination Metadata

List endpoints return a JSON array by default. Set `include_pagination_metadata = True` on a view to return metadata together with the list items:

```json
{
  "items": [],
  "total": 123,
  "page": 2,
  "page_size": 50,
  "total_pages": 3
}
```

`page`, `page_size`, and `total_pages` are populated only when pagination is active: the client sent `?page=` / `?page_size=`, or the view set `default_page_size`. Without pagination, those fields are `null`.

## Endpoint Decorators

Use these decorators on methods in a view class:

| Decorator | HTTP Method | Default Status |
|---|---|---|
| `@fr.get(path)` | `GET` | `200` |
| `@fr.post(path)` | `POST` | `201` |
| `@fr.patch(path)` | `PATCH` | `200` |
| `@fr.put(path)` | `PUT` | `200` |
| `@fr.delete(path)` | `DELETE` | `204` |
| `@fr.route(path, ...)` | Custom | As configured |

The shorthand decorators explicitly set the default status code shown. Pass `status_code=` to override it.

Other keyword arguments pass through to FastAPI route registration: `response_model=`, `dependencies=`, `responses=`, `tags=`, and other `APIRouter.add_api_route()` options.

`@fr.put(...)` is available for custom endpoints, but default generated update endpoints use `PATCH`.

## Route Exclusion

To disable generated endpoints on a view, use:

```python
@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    exclude_routes = [fr.ViewRoute.DELETE, fr.ViewRoute.UPDATE]
```

Valid route values for exclusion: `fr.ViewRoute.GET_MANY`, `fr.ViewRoute.GET_ONE`, `fr.ViewRoute.CREATE`, `fr.ViewRoute.UPDATE`, `fr.ViewRoute.DELETE`.

`exclude_routes` accepts `ViewRoute` values or the equivalent route-shell method names, such as `"delete_endpoint"`.

## Response Modeling

For generated CRUD endpoints:
- Response schema defaults to `schema` (or an auto-generated `*Read` schema when omitted).
- Input schema for `POST` defaults to schema without read-only fields (`schema_create`, generated as `*Create`).
- Input schema for `PATCH` defaults to optionalized schema (`schema_update`, generated as `*Update`).
- Alias-aware serialization is applied so response payload keys follow schema aliases.

## Key Public Symbols

### Model Base Classes

| Symbol | Description |
|---|---|
| `fr.DataclassBase` | SQLAlchemy declarative base with dataclass semantics and auto snake_case table names. |
| `fr.IDBase` | Convenience alias combining `DataclassBase` with an auto-incrementing integer `id` primary key. |
| `fr.TimestampsMixin` | Dataclass mixin adding `created_at` / `updated_at` to any `DataclassBase` subclass. |
| `fr.IDMixin` | Dataclass mixin adding integer `id` to a custom `DataclassBase` subclass. |
| `fastapi_restly.models.CASCADE_ALL_ASYNC` | Cascade string for use with `relationship(cascade=...)` in async SQLAlchemy models. Equivalent to `"save-update, merge, delete, expunge"`. SQLAlchemy's default `"all"` includes `"refresh-expire"` which is incompatible with async sessions. Import from `fastapi_restly.models` (not exposed at the top level). |
| `fastapi_restly.models.CASCADE_ALL_DELETE_ORPHAN_ASYNC` | Like `CASCADE_ALL_ASYNC` but also includes `"delete-orphan"`. |

FastAPI-Restly also works with ordinary SQLAlchemy models that inherit from your own `DeclarativeBase`. Use `fr.IDBase` for Restly's dataclass convenience base; bring your own base for standard constructor semantics or existing model layers.

`RestView` and `AsyncRestView` assume one scalar resource identifier at `/{id}`. The column can have another name when you provide explicit schemas and `id_type`, but the generated CRUD routes, `IDSchema`, `IDRef`, React Admin, and OpenAPI identity shape all remain scalar-id contracts. For composite keys, use `fr.View` and explicit routes such as `@fr.get("/{tenant_id}/{slug}")`.

### Schema Classes and Utilities

| Symbol | Description |
|---|---|
| `fr.BaseSchema` | Thin Pydantic base equivalent to `class BaseSchema(pydantic.BaseModel): model_config = pydantic.ConfigDict(from_attributes=True)`. Plain Pydantic models are also accepted for explicit create/update schemas. |
| `fr.IDSchema` | Response-schema base class that adds the resource's own read-only `id` field. |
| `fr.IDRef[Model]` | Scalar FK reference type. Wire format is the raw id (`5`) on request and response; dict input (`{"id": 5}`) is also accepted. Use this for typical REST FK fields and React Admin scalar id arrays. |
| `fr.IDSchema[Model]` | Nested relationship-object field type. Wire format is `{"id": 5}` on request and response. Use this when a client expects relationship objects instead of scalar FK fields. |
| `fr.TimestampsSchemaMixin` | Pydantic mixin adding read-only `created_at` / `updated_at` fields to a schema. |
| `fr.ReadOnly[T]` | Type annotation marker. Fields annotated `ReadOnly[T]` are excluded from create/update inputs. |
| `fr.WriteOnly[T]` | Type annotation marker. Fields annotated `WriteOnly[T]` are stripped by `self.to_response_schema(obj)`, which the generated CRUD and ReactAdmin routes use. Direct FastAPI/Pydantic serialization treats it as schema metadata only. |
| `fastapi_restly.schemas.create_schema_from_model(model)` | Auto-generate a Pydantic schema from a SQLAlchemy model. Useful for scaffolding, prototypes, and internal tools; prefer explicit schemas for stable public API contracts. Import from `fastapi_restly.schemas`; it is intentionally not exported at the top level. |

### View Classes

| Symbol | Description |
|---|---|
| `fr.View` | Base class for all class-based views. Subclass this directly when you do not need CRUD — add endpoints with `@fr.get`, `@fr.post`, etc. |
| `fastapi_restly.views.BaseRestView` | Supported advanced base class for custom CRUD foundations shared by sync and async views. Import from `fastapi_restly.views`; it is intentionally not exported at the top level. |
| `fr.AsyncRestView` | Async CRUD view. Use with async SQLAlchemy sessions. |
| `fr.RestView` | Sync CRUD view. Use with sync SQLAlchemy sessions. |
| `fr.ListingResult` | Value object returned by `get_many` (and `handle_get_many`), with `.objects`, `.total_count`, and `.query_params`, before `to_listing_response` formats the HTTP response. |
| `fr.AsyncReactAdminView` | Async CRUD view that speaks the `ra-data-simple-rest` wire contract used by [react-admin](https://marmelab.com/react-admin/). See [How-To: React Admin Integration](howto_react_admin.md). |
| `fr.ReactAdminView` | Sync variant of `AsyncReactAdminView`. |

### View Method Surface

Each CRUD verb on `RestView` / `AsyncRestView` is split into three tiers, so you
can override the layer you need:

1. **Route shell** (`<verb>_endpoint`) — the wire boundary: the `@route`, the
   FastAPI signature / `response_model`, and the call to `to_response`. Override
   only to change the HTTP contract.
2. **Request handler** (`handle_<verb>`) — the request logic: it runs
   `authorize` and the commit bracket (`before_commit` → commit →
   `after_commit`) and returns the domain object. Override to change
   orchestration or timing without re-declaring the route.
3. **Business method** (`<verb>`) — the domain operation (build / apply / save).
   It is **auth-free** and **commit-free**, and is the usual override point.
   The handler owns the commit.

Alongside the tiers are cross-cutting **override points** (`build_query`,
`apply_query_params`, `count`, `authorize`,
`before_commit` / `after_commit`, `to_response`, `snapshot`) and **domain
utilities** you call instead of override (`make_new_object`, `update_object`,
`save_object`, `delete_object`). `make_new_object` / `update_object` are also
the cooperative override point for field stamps.

On `AsyncRestView` every method below is `async`; the signatures are otherwise identical.

| Tier / kind | Method | Signature | Return | Purpose |
|---|---|---|---|---|
| Route shell | `get_many_endpoint` | `(query_params)` | response schema list or pagination envelope | `GET /`; validates query parameters and serializes the listing result via `to_response`. |
| Route shell | `get_one_endpoint` | `(id)` | response schema | `GET /{id}`; serializes one retrieved object. |
| Route shell | `create_endpoint` | `(schema_obj)` | response schema | `POST /`; serializes the created object. |
| Route shell | `update_endpoint` | `(id, schema_obj)` | response schema | `PATCH /{id}`; serializes the updated object. |
| Route shell | `delete_endpoint` | `(id)` | `fastapi.Response` | `DELETE /{id}`; returns `204` by default. |
| Request handler | `handle_get_many` | `(query_params)` | `ListingResult[Model]` | Run `authorize("get_many")`, then `get_many`. |
| Request handler | `handle_get_one` | `(id)` | `Model` | Load through `get_one` (scoped, 404), then `authorize("get_one", obj=...)`. Reusable from a custom action as "scoped load + 404 + read-auth". |
| Request handler | `handle_create` | `(schema_obj)` | `Model` | Authorize, run `create`, then the commit bracket. |
| Request handler | `handle_update` | `(id, schema_obj)` | `Model` | Load, authorize, snapshot, run `update`, then the commit bracket. |
| Request handler | `handle_delete` | `(id)` | `None` | Load, authorize, snapshot, run `delete`, then the commit bracket. |
| Custom-action bracket | `write_action` | `(action, *, obj=None, data=None)` | context manager | `async with self.write_action("publish", obj=...): ...` — runs the full bracket (authorize + snapshot on enter; before_commit → commit → after_commit on exit) around your inline mutation. For a custom write *action* that isn't a plain create/update/delete; deposit a create's new object on the yielded handle's `.obj`. Shares its implementation with the CRUD handlers via the self-free `run_write_action` (in `fastapi_restly.views`). |
| Business method | `get_many` | `(query_params)` | `ListingResult[Model]` | Scoped, filtered, paginated page plus total count, via `build_query` + `apply_query_params` + `count`. Auth-free. |
| Business method | `get_one` | `(id)` | `Model` | Load one row through `build_query` or raise `fr.NotFound`. Visibility comes from `build_query`, so a hidden row is a clean 404 for every caller. Auth-free. |
| Business method | `create` | `(schema_obj)` | `Model` | Build a new object and save it. Commit-free — the usual create override point. |
| Business method | `update` | `(obj, schema_obj)` | `Model` | Apply the update payload to `obj` and save it. Commit-free. |
| Business method | `delete` | `(obj)` | `None` | Delete `obj`. Override (e.g. on a soft-delete mixin) to flip a timestamp instead. |
| Override point | `build_query` | `()` | `sqlalchemy.Select` | Base read query shared by `get_many`, `count`, and `get_one` — add `WHERE` clauses here for scope/soft-delete/visibility. |
| Override point | `apply_query_params` | `(query, query_params)` | `sqlalchemy.Select` | Apply URL filter/sort/pagination to `query`. Override for a non-default URL grammar. |
| Override point | `count` | `(query)` | `int` | Total for the list, ignoring ordering/pagination. Override for estimated counts on huge tables. |
| Override point | `authorize` | `(action, obj=None, data=None)` | `None` | Gate a verb. A no-op by default; override to enforce policy and raise `fr.Forbidden` / `fr.NotFound` to reject. Row *visibility* belongs in `build_query`. |
| Override point | `before_commit` | `(action, new, old=None)` | `None` | In-transaction side effect (outbox/audit rows), atomic with the write. `old` is the pre-mutation snapshot dict. |
| Override point | `after_commit` | `(action, new, old=None)` | `None` | Post-commit side effect (email, webhook, cache invalidation). `old` enables dirty detection. |
| Override point | `to_response` | `(obj_or_list, shape=ResponseShape.SINGLE)` | response payload | The single wire-level response method, called by the route shells with the wire `ResponseShape` (`SINGLE` / `LISTING` / `EMPTY`) — not the write action. Override for envelopes or custom status codes; for a per-verb HTTP contract change, override that verb's route shell. |
| Override point | `snapshot` | `(obj)` | `dict[str, Any]` | Frozen capture of an object's column values at load time, passed as `old` to the commit hooks. |
| Helper | `to_response_schema` | `(obj)` | response schema | Validate and serialize an ORM object with Restly's alias/reference/write-only handling. |
| Helper | `to_listing_response` | `(query_params, listing_result)` | response schema list or pagination envelope | Serialize a `ListingResult` into the configured list HTTP response shape. |
| Helper | `to_paginated_listing_response` | `(query_params, listing_result)` | pagination envelope | Serialize a `ListingResult` into the paginated list response shape. |
| Domain utility | `make_new_object` | `(schema_obj)` | `Model` | Build and stage a new object without flushing. The cooperative override point for stamping extra fields on create — call `super()`, then mutate the returned object. |
| Domain utility | `update_object` | `(obj, schema_obj)` | `Model` | Apply writable fields without flushing. The cooperative override point for stamping extra fields on update — call `super()`, then mutate the returned object. |
| Domain utility | `save_object` | `(obj)` | `Model` | Flush and refresh a staged object. Does not commit — `handle_<verb>` owns the commit. |
| Domain utility | `delete_object` | `(obj)` | `None` | Delete and flush an existing object. Does not commit. |

Internal methods prefixed with `_`, such as `_reject_unknown_query_params`, are implementation details even though they are visible on instances.

See [Class-Based Views](class_based_views.md#the-view-hierarchy) for the class hierarchy and [How-To: Override Endpoints](howto_override_endpoints.md) for examples of choosing which tier to override.

`fr.View` class attributes:

| Attribute | Type | Description |
|---|---|---|
| `prefix` | `ClassVar[str]` | URL prefix for all routes in the view (e.g. `"/users"`). Required. |
| `tags` | `ClassVar[Iterable[str] \| None]` | OpenAPI tags. The view class name is always added automatically; set this to add extra tags. |
| `dependencies` | `ClassVar[Iterable[Any] \| None]` | FastAPI dependencies applied to every route in the view. |
| `responses` | `ClassVar[dict[int, Any]]` | OpenAPI response overrides. Defaults to `{404: {"description": "Not found"}}`. |

### View Class Attributes

| Attribute | Type | Description |
|---|---|---|
| `schema` | `ClassVar[type[pydantic.BaseModel]]` | The read/response schema. If omitted, auto-generated from `model` as `ModelRead`. |
| `schema_create` | `ClassVar[type[pydantic.BaseModel]]` | Schema for `POST` input. Auto-derived by removing `ReadOnly` fields and named `ModelCreate`. |
| `schema_update` | `ClassVar[type[pydantic.BaseModel]]` | Schema for `PATCH` input. Auto-derived by making all writable fields optional and named `ModelUpdate`. |
| `model` | `ClassVar[type[DeclarativeBase]]` | The SQLAlchemy model class. |
| `id_type` | `ClassVar[type]` | Scalar primary-key type used in generated `GET /{id}`, `PATCH /{id}`, and `DELETE /{id}` routes. Defaults to `int`. Composite primary keys are not supported by the generated CRUD route contract; use `fr.View` for custom multi-part identities. |
| `include_pagination_metadata` | `ClassVar[bool]` | Set `True` to return the paginated metadata envelope. Defaults to `False`. |
| `exclude_routes` | `ClassVar[Iterable[str \| ViewRoute]]` | Route names to suppress. |
| `extra_query_params` | `ClassVar[Iterable[str]]` | Query keys to allow on the listing endpoint in addition to those derived from the response schema. Use for view-specific parameters consumed outside `apply_list_params` (e.g. `?include_deleted=true`). |
| `default_page_size` | `ClassVar[int \| None]` | Default `?page_size=` for list endpoints. `None` (the default) means "no implicit cap" — every matching row is returned. |
| `max_page_size` | `ClassVar[int]` | Upper bound for `?page_size=` on list endpoints. Values above are rejected with 422. Defaults to `1000`. |

### Advanced Object Helpers

These helpers build, update, delete, and save ORM objects from schemas. Use them outside view instance methods: custom routes, services, workers, or tests. Sync and async variants are exported at the top level and from `fastapi_restly.objects`.

| Symbol | Description |
|---|---|
| `fr.make_new_object(session, model_cls, schema_obj, schema_cls=None)` | Build a new `model_cls` instance from `schema_obj`, resolve any `IDRef[...]` / `IDSchema[...]` reference fields against the database, and add the object to `session`. **Does not flush.** Call `fr.save_object(session, obj)` afterwards to persist. |
| `fr.update_object(session, obj, schema_obj, schema_cls=None)` | Apply the schema's writable fields onto an existing ORM `obj` and resolve FK fields. **Does not flush.** Call `fr.save_object(session, obj)` afterwards to persist. |
| `fr.save_object(session, obj)` | Flush the session and refresh `obj` so server-side defaults and generated columns (PKs, timestamps) are populated. Returns `obj`. This is where create/update writes hit the database. |
| `fr.delete_object(session, obj)` | Delete `obj` and flush the session. |
| `fr.async_make_new_object(session, model_cls, schema_obj, schema_cls=None)` | Async equivalent of `fr.make_new_object`. Pass an `AsyncSession`. |
| `fr.async_update_object(session, obj, schema_obj, schema_cls=None)` | Async equivalent of `fr.update_object`. |
| `fr.async_save_object(session, obj)` | Async equivalent of `fr.save_object`. |
| `fr.async_delete_object(session, obj)` | Async equivalent of `fr.delete_object`. |

### View Instance Methods

Every `AsyncRestView` / `RestView` instance wraps the object helpers above, binding `self.session`, `self.model`, and `self.schema`. `AsyncRestView` uses async helpers; `RestView` uses sync helpers.

Use these in business methods and custom routes. For a model other than `self.model`, use the top-level `fr.*` or `fastapi_restly.objects` helpers.

| Method | Description |
|---|---|
| `self.to_response_schema(obj)` | Serialise an ORM object to the configured response schema, applying alias rules, stripping `WriteOnly` fields, and running Pydantic response validation. Override for custom projections or an intentional `model_construct()` fast path. |
| `self.make_new_object(schema_obj)` | Wraps `make_new_object` / `async_make_new_object` against `self.session`, `self.model`, `self.schema`. The cooperative override point for stamping extra fields on create — call `super()`, then mutate the returned object. **Does not flush** — call `self.save_object(obj)` afterwards. |
| `self.update_object(obj, schema_obj)` | Wraps `update_object` / `async_update_object`. The cooperative override point for stamping extra fields on update — call `super()`, then mutate the returned object. **Does not flush** — call `self.save_object(obj)` afterwards. |
| `self.save_object(obj)` | Wraps `save_object` / `async_save_object` against `self.session`. Flush + refresh; this is where create/update writes hit the database. It does **not** commit — `handle_<verb>` owns the commit. |
| `self.delete_object(obj)` | Wraps `delete_object` / `async_delete_object` against `self.session`. Delete + flush, no commit. |
| `self.build_query()` | Return the base SQLAlchemy `Select` used by `get_many`, `count`, and `get_one`. Override for tenant scope, soft-delete filters, and row-level visibility. Hidden rows return 404 from `GET /{id}` too. Call `super().build_query()` and chain `.where(...)` to compose. See [Composing views with mixins](howto_compose_views_with_mixins.md). |
| `self.apply_query_params(query, query_params)` | Apply URL filter/sort/pagination to an already-built `query`. Override for a non-default URL grammar. |
| `self.count(query)` | Return the total row count for an already-built list query. The default `get_many` applies list params once, passes that same query to `count`, and `count` removes `ORDER BY`, `LIMIT`, and `OFFSET` before counting. Override for estimated counts on huge tables. |
| `self.to_response(obj_or_list, shape=ResponseShape.SINGLE)` | The wire-level response method. `shape` is `SINGLE`, `LISTING`, or `EMPTY`, not the write-action name. Override for envelopes or shape-wide status behavior; per-verb HTTP changes belong in that verb's route shell. |
| `self.to_listing_response(query_params, listing_result)` | Convert a `fr.ListingResult` into either the default JSON array or the pagination metadata envelope, depending on `include_pagination_metadata`. Override this when only the list response shape needs to change. |
| `self.to_paginated_listing_response(query_params, listing_result)` | Convert a `fr.ListingResult` into the paginated response envelope with `items`, `total`, `page`, `page_size`, and `total_pages`. Called by `to_listing_response` when `include_pagination_metadata = True`; override this when only the paginated envelope should change. |

### Database

| Symbol | Description |
|---|---|
| `fr.AsyncSessionDep` | FastAPI `Depends`-compatible async session dependency. |
| `fr.SessionDep` | FastAPI `Depends`-compatible sync session dependency. |
| `fr.open_async_session()` | Open an async SQLAlchemy session context manager for use outside request handling, for example in background jobs or scripts. |
| `fr.open_session()` | Open a sync SQLAlchemy session context manager for use outside request handling, for example in background jobs or scripts. |
| `fr.configure(async_database_url=..., ...)` | Configure the framework. Accepts async/sync URLs, engines, session makers, custom session generators, and `warn_on_uncommitted`. |
| `fr.get_async_engine()` | Return the configured `AsyncEngine` instance. |
| `fr.get_engine()` | Return the configured sync `Engine` instance. |

Restly has one public process-wide configuration. Configure it once during application startup:

```python
fr.configure(async_database_url="sqlite+aiosqlite:///app.db")
```

`fr.configure(...)` must receive at least one setup option: an app, database URL, engine, session maker, custom session generator, or `warn_on_uncommitted` setting. A bare `fr.configure()` raises `TypeError`.

For multiple databases, use FastAPI and SQLAlchemy directly: add a custom dependency on a view, or pass a custom session generator to `fr.configure(...)`. Restly does not provide a public multi-context or multi-engine API. See [Use a custom session dependency on one view](howto_existing_project.md#use-a-custom-session-dependency-on-one-view).

Restly's write handlers own the commit: each runs `before_commit` → commit → `after_commit` around domain logic. Session dependencies do **not** commit on response; they roll back and close on exit.

A **custom write route** should use `self.write_action(...)` or reuse `handle_create` / `handle_update` / `handle_delete`. Commit manually only for shapes the bracket does not model, such as a batch write with one final commit.

Restly warns (`RestlyUncommittedChangesWarning`) when a request finishes with uncommitted session changes. Disable with `fr.configure(warn_on_uncommitted=False)`, or suppress a deliberate dry run with `session.info["_fr_suppress_uncommitted"] = True`.

Restly owns the commit. A custom `session_generator` / `sync_session_generator` controls session construction and cleanup, not commit ownership.

### Exceptions

There are two families. Configuration errors subclass `RestlyError`; request-time HTTP errors subclass `fastapi.HTTPException` via `RestlyHTTPError`. Typed classes let you target Restly errors with `app.add_exception_handler(...)`.

| Symbol | Description |
|---|---|
| `fr.RestlyError` | Base class for FastAPI-Restly framework (configuration-time) errors. |
| `fr.RestlyConfigurationError` | Raised when a public Restly helper needs configuration that has not been set up yet, such as calling `fr.open_session()` before `fr.configure(...)`. |
| `fr.RestlyHTTPError` | Base for Restly's request-time HTTP errors. Subclass of `fastapi.HTTPException`; each subclass sets a status code. |
| `fr.NotFound` | HTTP `404`. Raised by `get_one` when a row does not exist or is hidden by `build_query`; also raisable from `authorize` to hide a row's existence. |
| `fr.Forbidden` | HTTP `403`. Raise from an `authorize` override to reject a verb. |
| `fr.Conflict` | HTTP `409`. For request conflicts with the current resource state. |
| `fr.BadQueryParam` | HTTP `400`. For an invalid filter/sort/pagination query parameter. |

### Testing

| Symbol | Description |
|---|---|
| `fastapi_restly.testing.RestlyTestClient` | Sync test client wrapper around FastAPI's `TestClient` with default status-code assertions. It can test async FastAPI routes and `AsyncRestView` endpoints. |
| `fastapi_restly.testing.activate_savepoint_only_mode(make_session)` | **Intended for tests.** Wraps a session factory in savepoint-only mode so test data never commits to the database. Requires the session maker as argument. |
| `fastapi_restly.testing.deactivate_savepoint_only_mode(make_session)` | Restore normal session behavior after testing. |

### Default Exception Handling

FastAPI-Restly installs a default handler for SQLAlchemy `IntegrityError` on FastAPI apps. The handler translates database integrity conflicts — unique constraint, foreign-key, not-null, and check-constraint violations — into HTTP `409 Conflict` responses using FastAPI's normal error body shape:

```json
{
  "detail": "Unique constraint violated on user.email"
}
```

The exact `detail` text is best-effort and depends on the database driver. The handler recognizes common PostgreSQL SQLSTATE integrity codes and SQLite constraint messages; unknown dialects fall back to a generic conflict message.

Registration is automatic in either of these cases:

- `fr.configure(app=app, ...)` is called with the default
  `install_default_exception_handlers=True`.
- A view is registered directly on a `FastAPI` app with `fr.include_view(app)`.
  This fallback covers apps that configure database sessions separately.

Restly skips this default only when the app already has a `sqlalchemy.exc.IntegrityError` handler. Generic handlers do not block registration.

To opt out:

```python
fr.configure(
    app=app,
    async_database_url="sqlite+aiosqlite:///app.db",
    install_default_exception_handlers=False,
)
```

To use your own response format, register your `IntegrityError` handler before Restly installs its defaults:

```python
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

@app.exception_handler(IntegrityError)
async def integrity_error_handler(request, exc):
    return JSONResponse(
        status_code=409,
        content={"error": {"code": "constraint_conflict"}},
    )

fr.configure(app=app, async_database_url="sqlite+aiosqlite:///app.db")
```

## Important Limitations and Capabilities

- Nested schemas are supported for **responses** and relation filtering, including nested aliases
- Full nested schemas are **not** supported for create/update payloads by the default CRUD flow; write payloads must map directly to model fields, or use model-aware reference fields such as `*_id: IDRef[Model]` and relationship fields typed as `IDSchema[Model]`
- Ordinary SQLAlchemy `DeclarativeBase` models work with generated CRUD views
- UUID and other non-`int` scalar primary keys are supported through `id_type`, `IDRef[Model]`, and `IDSchema[Model]`
- Composite primary keys are not supported by generated `RestView` / `AsyncRestView` CRUD routes; use `fr.View` for custom route shapes

## Minimal Example

```python
import asyncio
import fastapi_restly as fr
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import Mapped

engine = create_async_engine("sqlite+aiosqlite:///app.db")
fr.configure(async_engine=engine)
app = FastAPI()

class User(fr.IDBase):
    name: Mapped[str]

@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User


async def init_models() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(fr.DataclassBase.metadata.create_all)


asyncio.run(init_models())
```

Generated endpoints:
- `GET /users/`
- `POST /users/`
- `GET /users/{id}`
- `PATCH /users/{id}`
- `DELETE /users/{id}`

## Full Python API (Autodoc)

```{toctree}
:maxdepth: 2

api/index
```
