# API Reference

This page documents:
- Generated HTTP endpoints and query behavior.
- Key public symbols with brief descriptions.
- Full Python API reference generated via Sphinx autodoc.

## Generated CRUD Endpoints

When you register a view with `@fr.include_view(app)`, both `fr.AsyncRestView` and `fr.RestView` expose the same default CRUD surface:

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
- `*_id: IDSchema[Model]` inputs are resolved to SQLAlchemy objects and validated against the database. The nested `id` accepts the related primary-key type, such as `int` or `UUID`.

## Query Parameters (List Endpoint)

`GET /{prefix}/` supports query modifiers through `fr.apply_query_modifiers(...)`.

### V1 (JSONAPI-style)
- Filtering: `?filter[name]=John&filter[age]=>21`
- OR-values: `?filter[id]=1,2,3` (comma-separated values are OR'd together)
- Sorting: `?sort=name,-created_at`
- Pagination: `?limit=10&offset=20` (no limit is applied when omitted)
- Contains: `?contains[name]=john` (multiple space-separated words are AND'd: all must be contained)
  - V1 uses schema field names, not aliases

### V2 (HTTP-style)
- Filtering: `?name=John&created_at__gte=2024-01-01`
  - Suffixes: `__gte`, `__lte`, `__gt`, `__lt`, `__ne`, `__isnull`, `__contains` (string fields only)
  - OR-values: `?id=1,2,3` (comma-separated values are OR'd together)
  - Flat aliased fields use the alias name. If `populate_by_name=True` is enabled, flat fields also accept the Python field name.
- Contains: `?name__contains=john` (multiple space-separated words are AND'd: all must be contained)
  - `%`, `_`, and `\\` are escaped before building the SQL `ILIKE`
- Sorting: `?order_by=name,-created_at`
- Pagination: `?page=2&page_size=10`
  - **Always applied:** even with no pagination params, V2 applies `LIMIT 100 OFFSET 0` by default.
  - Defaults: `page=1`, `page_size=100`.

Relation-filtering caveat for V2:
- The relation segment must still use the schema/model field name
- Only nested field segments may use aliases
- Example: `?author.authorName=Alice` can work, while `?writer.authorName=Alice` does not

### Query Modifier Version Configuration

The active version is controlled via:
- `fr.set_query_modifier_version(fr.QueryModifierVersion.V2)` — set globally
- `fr.get_query_modifier_version()` — read the current global setting
- `fr.use_query_modifier_version(version)` — context manager; preferred for tests and direct low-level helper calls

`fr.QueryModifierVersion` is an enum with two members: `QueryModifierVersion.V1` (default) and `QueryModifierVersion.V2`.

Views capture the active query-modifier version when they are registered with
`@fr.include_view(...)`. Set the global version before registering the view, or
set `query_modifier_version = fr.QueryModifierVersion.V2` on the view class directly
for explicit per-view behavior.

`fastapi_restly.query.create_query_param_schema(schema_cls)` is context-sensitive: it creates a V1 or V2
query-parameter schema depending on the currently active version. (It is not exposed at the top level —
import it from `fastapi_restly.query`.)

## Optional Pagination Metadata

List endpoints return a JSON array by default. Set `include_pagination_metadata = True`
on a view to return metadata together with the list items:

```json
{
  "items": [],
  "total": 123,
  "page": 1,
  "page_size": 100,
  "total_pages": 2,
  "limit": 100,
  "offset": 0
}
```

`page`, `page_size`, and `total_pages` are populated when:
- The view uses the V2 query interface (always), or
- A V1 view receives `?page=` or `?page_size=` as query parameters.

Otherwise on V1 views, those three fields stay `null` and `limit`/`offset` reflect the V1 `?limit=`/`?offset=` parameters if present.

## Endpoint Decorators

Use these decorators on methods in a view class:

| Decorator | HTTP Method | Default Status |
|---|---|---|
| `@fr.get(path)` | `GET` | `200` |
| `@fr.post(path)` | `POST` | `201` |
| `@fr.patch(path)` | `PATCH` | FastAPI default (`200`) |
| `@fr.put(path)` | `PUT` | FastAPI default (`200`) |
| `@fr.delete(path)` | `DELETE` | `204` |
| `@fr.route(path, ...)` | Custom | As configured |

`@fr.get()`, `@fr.post()`, and `@fr.delete()` explicitly set the default status code shown.
`@fr.patch()` and `@fr.put()` set only the HTTP method; FastAPI applies its own default of `200`.
Pass `status_code=` explicitly to either decorator to override.

`@fr.put(...)` is available for custom endpoints, but default generated update endpoints use `PATCH`.

## Route Exclusion

To disable generated endpoints on a view, use:

```python
@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    exclude_routes = ("delete", "patch")
```

Valid route names for exclusion: `"index"`, `"get"`, `"post"`, `"patch"`, `"delete"`.

`exclude_routes` is typed `ClassVar[tuple[str, ...]]`; a list literal is also accepted at runtime.

## Response Modeling

For generated CRUD endpoints:
- Response schema defaults to `schema` (or auto-generated schema when omitted).
- Input schema for `POST` defaults to schema without read-only fields (`creation_schema`).
- Input schema for `PATCH` defaults to optionalized schema (`update_schema`, via `PatchMixin`).
- Alias-aware serialization is applied so response payload keys follow schema aliases.

## Key Public Symbols

### Model Base Classes

| Symbol | Description |
|---|---|
| `fr.DataclassBase` | SQLAlchemy declarative base with dataclass semantics and auto snake_case table names. |
| `fr.IDBase` | Convenience alias combining `DataclassBase` with an auto-incrementing integer `id` primary key. |
| `fr.IDStampsBase` | Extends `IDBase` with `created_at` / `updated_at` timestamps (UTC-aware). |
| `fr.TimestampsMixin` | Dataclass mixin adding `created_at` / `updated_at` to any `DataclassBase` subclass. |
| `fr.PlainBase` | Alternative declarative base without dataclass semantics. |
| `fr.PlainIDBase` | Convenience alias combining `PlainBase` with an auto-incrementing integer `id` primary key. |
| `fr.PlainIDStampsBase` | Extends `PlainIDBase` with `created_at` / `updated_at` timestamps. |
| `fr.IDMixin` | Dataclass mixin adding integer `id` to a custom `DataclassBase` subclass. |
| `fr.PlainIDMixin` | Non-dataclass mixin adding integer `id` to a `PlainBase` subclass. |
| `fr.PlainTimestampsMixin` | Non-dataclass mixin adding `created_at` / `updated_at` to a `PlainBase` subclass. |
| `fr.get_one_or_create(model, session, **kwargs)` | Return the unique matching row or create it using a sync SQLAlchemy session. |
| `fr.async_get_one_or_create(model, session, **kwargs)` | Async variant of `get_one_or_create`. |
| `fastapi_restly.models.CASCADE_ALL_ASYNC` | Cascade string for use with `relationship(cascade=...)` in async SQLAlchemy models. Equivalent to `"save-update, merge, delete, expunge"`. SQLAlchemy's default `"all"` includes `"refresh-expire"` which is incompatible with async sessions. Import from `fastapi_restly.models` (not exposed at the top level). |
| `fastapi_restly.models.CASCADE_ALL_DELETE_ORPHAN_ASYNC` | Like `CASCADE_ALL_ASYNC` but also includes `"delete-orphan"`. |

### Schema Classes and Utilities

| Symbol | Description |
|---|---|
| `fr.BaseSchema` | Base Pydantic model with `from_attributes=True`. All schemas should inherit from this. |
| `fr.IDSchema[Model]` | Generic schema that serializes only the `id` of a related model. Used for FK inputs. |
| `fr.FlatIDSchema[Model]` | Variant of `IDSchema` that serializes as the raw `id` scalar instead of a `{"id": ...}` object (and also accepts a raw scalar on input). Use this for relationship list fields in response schemas to match React Admin / `ra-data-simple-rest` defaults — for example, `products: list[fr.FlatIDSchema[Product]]` serializes as `[1, 2, 3]` rather than `[{"id": 1}, ...]`. |
| `fr.IDStampsSchema` | Combines `IDSchema` with read-only `created_at` / `updated_at` fields. |
| `fr.TimestampsSchemaMixin` | Pydantic mixin adding read-only `created_at` / `updated_at` fields to a schema. |
| `fr.ReadOnly[T]` | Type annotation marker. Fields annotated `ReadOnly[T]` are excluded from create/update inputs. |
| `fr.WriteOnly[T]` | Type annotation marker. Fields annotated `WriteOnly[T]` are excluded from responses. |
| `fr.OmitReadOnlyMixin` | Mixin that strips `ReadOnly` fields from a schema subclass (used by `creation_schema`). |
| `fr.PatchMixin` | Mixin that makes all writable fields optional with `None` default (used by `update_schema`). |
| `fr.create_schema_from_model(model)` | Auto-generate a Pydantic schema from a SQLAlchemy model. |
| `fr.auto_generate_schema_for_view(view_cls, model_cls)` | Generate a schema for a view from its model, excluding relationship fields. Used internally by `include_view`. |
| `fr.resolve_ids_to_sqlalchemy_objects(session, schema_obj)` | Walk a schema instance, load `_id`-suffixed `IDSchema` fields from the database, and replace them with ORM objects. Called automatically during create/update. |

### View Classes

| Symbol | Description |
|---|---|
| `fr.View` | Base class for all class-based views. Subclass this directly when you do not need CRUD — add endpoints with `@fr.get`, `@fr.post`, etc. |
| `fr.AsyncRestView` | Async CRUD view. Use with async SQLAlchemy sessions. |
| `fr.RestView` | Sync CRUD view. Use with sync SQLAlchemy sessions. |
| `fr.AsyncReactAdminView` | Async CRUD view that speaks the `ra-data-simple-rest` wire contract used by [react-admin](https://marmelab.com/react-admin/). See [How-To: React Admin Integration](howto_react_admin.md). |
| `fr.ReactAdminView` | Sync variant of `AsyncReactAdminView`. |

`fr.View` class attributes:

| Attribute | Type | Description |
|---|---|---|
| `prefix` | `ClassVar[str]` | URL prefix for all routes in the view (e.g. `"/users"`). Required. |
| `tags` | `ClassVar[list[str] \| None]` | OpenAPI tags. The view class name is always added automatically; set this to add extra tags. |
| `dependencies` | `ClassVar[list[Any] \| None]` | FastAPI dependencies applied to every route in the view. |
| `responses` | `ClassVar[dict[int, Any]]` | OpenAPI response overrides. Defaults to `{404: {"description": "Not found"}}`. |

### View Class Attributes

| Attribute | Type | Description |
|---|---|---|
| `schema` | `ClassVar[type[BaseSchema]]` | The primary Pydantic schema for responses. If omitted, auto-generated from `model`. |
| `creation_schema` | `ClassVar[type[BaseSchema]]` | Schema for `POST` input. Auto-derived by removing `ReadOnly` fields. |
| `update_schema` | `ClassVar[type[BaseSchema]]` | Schema for `PATCH` input. Auto-derived by making all writable fields optional. |
| `model` | `ClassVar[type[DeclarativeBase]]` | The SQLAlchemy model class. |
| `id_type` | `ClassVar[type]` | Primary key type used in generated `GET /{id}`, `PATCH /{id}`, and `DELETE /{id}` routes. Defaults to `int`. |
| `include_pagination_metadata` | `ClassVar[bool]` | Set `True` to return the paginated metadata envelope. Defaults to `False`. |
| `exclude_routes` | `ClassVar[tuple[str, ...]]` | Route names to suppress. |
| `query_modifier_version` | `ClassVar[QueryModifierVersion]` | Per-view query style override. Defaults to the global setting at registration time. |

### View Instance Methods

Methods available on every `AsyncRestView` / `RestView` instance (inherited from
the internal abstract CRUD base). Use these inside `on_*` hooks or custom route
methods to reuse the framework's plumbing.

| Method | Description |
|---|---|
| `self.to_response_schema(obj)` | Serialise an ORM object to the configured response schema, applying alias rules and stripping `WriteOnly` fields. Returns a Pydantic model instance. |
| `self.make_new_object(schema_obj, schema_cls=None)` | Async on `AsyncRestView`, sync on `RestView`. Build a new model instance from a schema, resolve any `*_id: IDSchema[...]` fields against the database, add it to `self.session`, flush, and return the persisted object. |
| `self.update_object(obj, schema_obj, schema_cls=None)` | Apply the schema's writable fields to an existing object, resolve FK fields, flush, and return the object. |
| `self.save_object(obj)` | Flush the session and refresh `obj` from the database, then return it. Override to inject behaviour right before/after a write. |
| `self.delete_object(obj)` | Delete `obj` via the session and flush. |
| `self.count_index(query_params)` | Return the total row count for the current list query (after filters, before pagination). Called by the default `index` only when `include_pagination_metadata = True`; available for use in replacement routes regardless. |

### View Free Functions

These module-level functions mirror the instance methods on `AsyncRestView` / `RestView` but take an explicit session argument. Use them when you need the same logic outside a view class.

| Symbol | Description |
|---|---|
| `fr.make_new_object(session, model_cls, schema_obj, schema_cls=None)` | Create a new model instance from a schema, resolve FK `IDSchema` fields, add to session, and return the object. |
| `fr.update_object(session, obj, schema_obj, schema_cls=None)` | Apply schema fields to an existing ORM object, resolve FK fields, flush, and return the object. |
| `fr.save_object(session, obj)` | Flush the session and refresh `obj` from the database, then return it. |

### Database

| Symbol | Description |
|---|---|
| `fr.AsyncSessionDep` | FastAPI `Depends`-compatible async session dependency. |
| `fr.SessionDep` | FastAPI `Depends`-compatible sync session dependency. |
| `fr.configure(async_database_url=..., ...)` | Configure the framework. Accepts async/sync URLs, engines, session makers, or custom session generators. |
| `fr.get_async_engine()` | Return the configured `AsyncEngine` instance. |
| `fr.get_engine()` | Return the configured sync `Engine` instance. |
| `fr.activate_savepoint_only_mode(make_session)` | **Intended for tests.** Wraps the session factory in a savepoint so test data never commits to the database. Each test rolls back instantly without touching the real data. Requires the session maker as argument. |
| `fr.deactivate_savepoint_only_mode(make_session)` | Restore normal session behavior after testing. |
| `fr.use_fr_globals(globals_obj)` | Context manager that swaps the global state for test isolation. |
| `fr.get_fr_globals()` | Return the current `FRGlobals` instance (engine, session factory, etc.). |

## Important Limitations and Capabilities

- Nested schemas are supported for **responses** and relation filtering, including nested aliases
- Nested schemas are **not** supported for create/update payloads; write payloads must still map directly to model fields or use `*_id: IDSchema[Model]`
- `fr.PlainBase` / `fr.PlainIDBase` models work with generated CRUD views
- UUID and other non-`int` primary keys are supported through `id_type` and `IDSchema[Model]`

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
