# API Reference

This page documents:
- Generated HTTP endpoints and query behavior.
- Key public symbols with brief descriptions.
- Full Python API reference generated via Sphinx autodoc.

## Generated CRUD Endpoints

When you register a view with `fr.include_view(app, ViewClass)` (or the small-app decorator shortcut `@fr.include_view(app)`), both `fr.AsyncRestView` and `fr.RestView` expose the same default CRUD surface:

| Method | Path | Purpose | Default Status |
|---|---|---|---|
| `GET` | `/{prefix}/` | List resources | `200` |
| `POST` | `/{prefix}/` | Create resource | `201` |
| `GET` | `/{prefix}/{id}` | Get resource by ID | `200` |
| `PATCH` | `/{prefix}/{id}` | Partial update | `200` |
| `DELETE` | `/{prefix}/{id}` | Delete resource | `204` |

Notes:
- Update semantics are `PATCH` (partial update), not `PUT`. (`AsyncReactAdminView` / `ReactAdminView` additionally expose `PUT /{id}` to match `ra-data-simple-rest`; see [How-To: React Admin Integration](howto_react_admin.md).)
- `GET /{id}` and `DELETE /{id}` return `404` when the object is not found.
- Read-only schema fields are ignored on create/update.
- `*_id: IDRef[Model]` inputs are resolved to SQLAlchemy objects and validated against the database. The scalar id accepts the related primary-key type, such as `int` or `UUID`.

## Query Parameters (List Endpoint)

`GET /{prefix}/` exposes a stable URL parameter dialect — the **list
parameters** — derived from the response schema. This contract is part
of the public API: parameter keys follow the response schema's public
field names (aliases when set, Python names otherwise), end-to-end,
including dotted relation paths.

- Filtering: `?name=John&created_at__gte=2024-01-01`
  - Suffixes: `__gte`, `__lte`, `__gt`, `__lt`, `__ne`, `__isnull`, `__contains`, `__icontains` (contains operators are string fields only)
  - OR-values (IN): `?id=1,2,3` (comma-separated values are OR-combined for `eq`)
  - NOT-IN: `?status__ne=archived,deleted` (comma-separated values are AND-combined for `__ne`)
  - Aliased fields use only the alias as the URL key; the Python field name is not accepted (``populate_by_name`` only affects body parsing, not the URL surface).
- Contains: `?name__contains=John` (case-sensitive where the SQL backend supports that distinction)
- IContains: `?name__icontains=john` (case-insensitive)
  - Repeat the parameter to AND multiple terms — this is the precise form: `?name__contains=john&name__contains=doe`.
  - As a convenience, whitespace inside one value is also AND-split: `?name__contains=john%20doe` is equivalent.
  - `%`, `_`, and `\\` are escaped before building the SQL `LIKE` / `ILIKE`.
- Sorting: `?sort=name,-created_at`
- Pagination: `?page=2&page_size=10`
  - **Opt-in.** Omitting `page_size` returns every matching row (no implicit cap).
  - For public/production endpoints, set `default_page_size` and `max_page_size` explicitly on the view class.

**Unknown query keys are rejected.** Generated list endpoints validate
the request's query string against the schema's declared parameters. Any
key that isn't part of the generated schema — a typoed filter, a Python
field name on an aliased field, an operator suffix that wasn't emitted
for the field's type (e.g. ``__gte`` on a boolean) — produces a 422
response with a FastAPI-style validation envelope. This prevents typos
from silently widening the result set. To allow extra query keys that a
view consumes outside the schema (e.g. an ``?include_deleted=true``
escape hatch on a soft-delete mixin), declare them on the view class:

```python
class UserView(fr.AsyncRestView):
    extra_query_params = ("include_deleted",)
```

Relation filtering uses dot notation, and aliases apply to every
segment of the path. If `ArticleRead.author` has `Field(alias="writer")`
and `AuthorRead.name` has `Field(alias="authorName")`, the URL key is
`writer.authorName`. Canonical Python names are not exposed.

### Low-level helpers

`fr.create_list_params_schema(schema_cls, *, default_page_size=None,
max_page_size=1000)` and `fr.apply_list_params(params, query, model,
schema_cls)` are the primitives behind the generated endpoints. The
happy path is to define a `RestView` / `AsyncRestView` and let the
framework wire them up — the generated FastAPI endpoint validates
incoming requests against the params schema before the SQL clauses are
applied. Reach for these helpers directly only when you need to apply
list parameters to a custom (non-`RestView`) endpoint, and pass a
validated `create_list_params_schema(...)` instance rather than a raw
`QueryParams` so pagination/filter bounds are enforced.

## Optional Pagination Metadata

List endpoints return a JSON array by default. Set `include_pagination_metadata = True`
on a view to return metadata together with the list items:

```json
{
  "items": [],
  "total": 123,
  "page": 2,
  "page_size": 50,
  "total_pages": 3
}
```

`page`, `page_size`, and `total_pages` are populated when the request
was actually paginated — that is, when the client sent `?page=` /
`?page_size=`, or when the view sets a non-`None` `default_page_size`.
When pagination is not engaged the fields stay `null` and only `total`
reflects the full result count.

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

The shorthand decorators explicitly set the default status code shown. Pass
`status_code=` to override it.

`@fr.put(...)` is available for custom endpoints, but default generated update endpoints use `PATCH`.

## Route Exclusion

To disable generated endpoints on a view, use:

```python
@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    exclude_routes = ["delete", "patch"]
```

Valid route names for exclusion: `"index"`, `"get"`, `"post"`, `"patch"`, `"delete"`.

`exclude_routes` accepts any iterable of route-name strings, such as a list or tuple.

## Response Modeling

For generated CRUD endpoints:
- Response schema defaults to `schema` (or an auto-generated `*Read` schema when omitted).
- Input schema for `POST` defaults to schema without read-only fields (`creation_schema`, generated as `*Create`).
- Input schema for `PATCH` defaults to optionalized schema (`update_schema`, generated as `*Update`).
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

FastAPI-Restly also works with ordinary SQLAlchemy declarative models that
inherit from your own `sqlalchemy.orm.DeclarativeBase`. Use `fr.IDBase` when you
want Restly's dataclass-oriented convenience base; bring your own SQLAlchemy
base when you prefer standard declarative constructor semantics or are adding
Restly to an existing model layer.

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
| `fr.AsyncReactAdminView` | Async CRUD view that speaks the `ra-data-simple-rest` wire contract used by [react-admin](https://marmelab.com/react-admin/). See [How-To: React Admin Integration](howto_react_admin.md). |
| `fr.ReactAdminView` | Sync variant of `AsyncReactAdminView`. |

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
| `creation_schema` | `ClassVar[type[pydantic.BaseModel]]` | Schema for `POST` input. Auto-derived by removing `ReadOnly` fields and named `ModelCreate`. |
| `update_schema` | `ClassVar[type[pydantic.BaseModel]]` | Schema for `PATCH` input. Auto-derived by making all writable fields optional and named `ModelUpdate`. |
| `model` | `ClassVar[type[DeclarativeBase]]` | The SQLAlchemy model class. |
| `id_type` | `ClassVar[type]` | Primary key type used in generated `GET /{id}`, `PATCH /{id}`, and `DELETE /{id}` routes. Defaults to `int`. |
| `include_pagination_metadata` | `ClassVar[bool]` | Set `True` to return the paginated metadata envelope. Defaults to `False`. |
| `exclude_routes` | `ClassVar[Iterable[str]]` | Route names to suppress. |
| `extra_query_params` | `ClassVar[Iterable[str]]` | Query keys to allow on the index endpoint in addition to those derived from the response schema. Use for view-specific parameters consumed outside `apply_list_params` (e.g. an `?include_deleted=true` escape hatch). |
| `default_page_size` | `ClassVar[int \| None]` | Default `?page_size=` for list endpoints. `None` (the default) means "no implicit cap" — every matching row is returned. |
| `max_page_size` | `ClassVar[int]` | Upper bound for `?page_size=` on list endpoints. Values above are rejected with 422. Defaults to `1000`. |

### CRUD Utility Free Functions

These module-level functions are the primitive surface for building, updating,
and explicitly saving ORM objects from schemas. Use them anywhere you have a
session — inside `handle_*` handlers, in custom routes, in services, or in test
setup. Each variant exists in both sync and async form, matching the session type
you have on hand.

| Symbol | Description |
|---|---|
| `fr.make_new_object(session, model_cls, schema_obj, schema_cls=None)` | Build a new `model_cls` instance from `schema_obj`, resolve any `IDRef[...]` / `IDSchema[...]` reference fields against the database, and add the object to `session`. **Does not flush.** Call `fr.save_object(session, obj)` afterwards to persist. |
| `fr.update_object(session, obj, schema_obj, schema_cls=None)` | Apply the schema's writable fields onto an existing ORM `obj` and resolve FK fields. **Does not flush.** Call `fr.save_object(session, obj)` afterwards to persist. |
| `fr.save_object(session, obj)` | Flush the session and refresh `obj` so server-side defaults and generated columns (PKs, timestamps) are populated. Returns `obj`. This is where writes actually hit the database. |
| `fr.async_make_new_object(session, model_cls, schema_obj, schema_cls=None)` | Async equivalent of `fr.make_new_object`. Pass an `AsyncSession`. |
| `fr.async_update_object(session, obj, schema_obj, schema_cls=None)` | Async equivalent of `fr.update_object`. |
| `fr.async_save_object(session, obj)` | Async equivalent of `fr.save_object`. |

### View Instance Methods

Every `AsyncRestView` / `RestView` instance exposes ergonomic wrappers
around the free functions above. The wrappers bind `self.session`,
`self.model`, and `self.schema` so the dominant case
(`self.make_new_object(schema_obj)`) doesn't have to thread them
explicitly. The async/sync split is implicit: `AsyncRestView.make_new_object`
calls `async_make_new_object` under the hood, `RestView.make_new_object`
calls the sync version.

Use these inside `handle_*` handlers or custom route methods. When you need to
work with a model that isn't `self.model` (e.g. creating a sibling row
in a custom endpoint) reach for the free functions instead.

| Method | Description |
|---|---|
| `self.to_response_schema(obj)` | Serialise an ORM object to the configured response schema, applying alias rules, stripping `WriteOnly` fields, and running Pydantic response validation. Override for custom projections or an intentional `model_construct()` fast path. |
| `self.make_new_object(schema_obj, schema_cls=None)` | Wraps `fr.make_new_object` / `fr.async_make_new_object` against `self.session`, `self.model`, `self.schema`. **Does not flush** — call `self.save_object(obj)` afterwards. |
| `self.update_object(obj, schema_obj, schema_cls=None)` | Wraps `fr.update_object` / `fr.async_update_object`. **Does not flush** — call `self.save_object(obj)` afterwards. |
| `self.save_object(obj)` | Wraps `fr.save_object` / `fr.async_save_object` against `self.session`. Flush + refresh; this is where writes actually hit the database. |
| `self.delete_object(obj)` | Delete `obj` via `self.session` and flush. |
| `self.build_list_query()` | Return the base SQLAlchemy `Select` used by both `handle_list` and `count_index`. Defaults to `sqlalchemy.select(self.model)`. Override to add `WHERE` clauses that should apply to listing **and** its pagination total — tenant scoping, soft-delete filtering, permission-based row visibility. Call `super().build_list_query()` and chain `.where(...)` to compose with base-class or mixin filters. See [Composing views with mixins](howto_compose_views_with_mixins.md). |
| `self.count_index(query_params)` | Return the total row count for the current list query (after filters, before pagination). Called by the default `index` only when `include_pagination_metadata = True`; available for use in replacement routes regardless. Consults `build_list_query()` so list and count stay in sync. |

### Database

| Symbol | Description |
|---|---|
| `fr.AsyncSessionDep` | FastAPI `Depends`-compatible async session dependency. |
| `fr.SessionDep` | FastAPI `Depends`-compatible sync session dependency. |
| `fr.open_async_session()` | Open an async SQLAlchemy session context manager for use outside request handling, for example in background jobs or scripts. |
| `fr.open_session()` | Open a sync SQLAlchemy session context manager for use outside request handling, for example in background jobs or scripts. |
| `fr.configure(async_database_url=..., ...)` | Configure the framework. Accepts async/sync URLs, engines, session makers, custom session generators, and `commit_session_on_response`. |
| `fr.get_async_engine()` | Return the configured `AsyncEngine` instance. |
| `fr.get_engine()` | Return the configured sync `Engine` instance. |

Restly has one public process-wide configuration. Configure it once during
application startup:

```python
fr.configure(async_database_url="sqlite+aiosqlite:///app.db")
```

`fr.configure(...)` must receive at least one meaningful setup option, such as
an app for default exception-handler registration, a database URL, an engine, a
session maker, a custom session generator, or an explicit
`commit_session_on_response` policy. A bare `fr.configure()` call raises
`TypeError`.

Applications that need more than one database can still use FastAPI and
SQLAlchemy directly: provide a custom dependency on a view, or pass a custom
session generator to `fr.configure(...)`. Restly does not currently provide a
public multi-context or multi-engine API. See
[Use a custom session dependency on one view](howto_existing_project.md#use-a-custom-session-dependency-on-one-view)
for per-view session wiring.

By default, Restly commits sessions created by `AsyncSessionDep` / `SessionDep`
when an endpoint successfully produces a response. On FastAPI versions that
support dependency scopes, Restly requests function scope so this commit runs
before the response is sent. On older FastAPI versions, commit timing follows
FastAPI's default `yield` dependency cleanup timing and may run after the
response has been sent.

Set `commit_session_on_response=False` if your handlers should call
`commit()` / `rollback()` explicitly. If you pass `session_generator` or
`sync_session_generator`, Restly does not add commit/rollback behavior; that
custom generator owns the transaction lifecycle.

### Exceptions

| Symbol | Description |
|---|---|
| `fr.RestlyError` | Base class for FastAPI-Restly framework errors. |
| `fr.RestlyConfigurationError` | Raised when a public Restly helper needs configuration that has not been set up yet, such as calling `fr.open_session()` before `fr.configure(...)`. |

### Testing

| Symbol | Description |
|---|---|
| `fastapi_restly.testing.RestlyTestClient` | Sync test client wrapper around FastAPI's `TestClient` with default status-code assertions. It can test async FastAPI routes and `AsyncRestView` endpoints. |
| `fastapi_restly.testing.activate_savepoint_only_mode(make_session)` | **Intended for tests.** Wraps a session factory in savepoint-only mode so test data never commits to the database. Requires the session maker as argument. |
| `fastapi_restly.testing.deactivate_savepoint_only_mode(make_session)` | Restore normal session behavior after testing. |

### Default Exception Handling

FastAPI-Restly installs a default handler for SQLAlchemy `IntegrityError` on
FastAPI apps. The handler translates database integrity conflicts — unique
constraint, foreign-key, not-null, and check-constraint violations — into HTTP
`409 Conflict` responses using FastAPI's normal error body shape:

```json
{
  "detail": "Unique constraint violated on user.email"
}
```

The exact `detail` text is best-effort and depends on the database driver. The
handler recognizes common PostgreSQL SQLSTATE integrity codes and SQLite
constraint messages; unknown dialects fall back to a generic conflict message.

Registration is automatic in either of these cases:

- `fr.configure(app=app, ...)` is called with the default
  `install_default_exception_handlers=True`.
- A view is registered directly on a `FastAPI` app with `fr.include_view(app)`.
  This fallback covers apps that configure database sessions separately.

Restly only skips this default when the app already has a handler registered
specifically for `sqlalchemy.exc.IntegrityError`. Other handlers, such as a
generic `Exception` handler, do not prevent Restly from registering its
`IntegrityError` handler.

To opt out:

```python
fr.configure(
    app=app,
    async_database_url="sqlite+aiosqlite:///app.db",
    install_default_exception_handlers=False,
)
```

To use your own response format, register your `IntegrityError` handler before
Restly installs its defaults:

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
- UUID and other non-`int` primary keys are supported through `id_type`, `IDRef[Model]`, and `IDSchema[Model]`

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
