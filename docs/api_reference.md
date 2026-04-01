# API Reference

This page documents:
- Generated HTTP endpoints and query behavior.
- Key public symbols with brief descriptions.
- Full Python API reference generated via Sphinx autodoc.

## Generated CRUD Endpoints

When you register a view with `@fr.include_view(app)`, both `fr.AsyncAlchemyView` and `fr.AlchemyView` expose the same default CRUD surface:

| Method | Path | Purpose | Default Status |
|---|---|---|---|
| `GET` | `/{prefix}/` | List resources | `200` |
| `POST` | `/{prefix}/` | Create resource | `201` |
| `GET` | `/{prefix}/{id}` | Get resource by ID | `200` |
| `PATCH` | `/{prefix}/{id}` | Partial update | `200` |
| `DELETE` | `/{prefix}/{id}` | Delete resource | `204` |

Notes:
- Update semantics are `PATCH` (partial update), not `PUT`.
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

### V2 (HTTP-style)
- Filtering: `?name=John&created_at__gte=2024-01-01`
  - Suffixes: `__gte`, `__lte`, `__gt`, `__lt`, `__isnull`, `__contains` (string fields only)
  - OR-values: `?id=1,2,3` (comma-separated values are OR'd together)
  - For aliased schemas, use the **alias name** as the query parameter, not the Python field name.
- Contains: `?name__contains=john` (multiple space-separated words are AND'd: all must be contained)
- Sorting: `?order_by=name,-created_at`
- Pagination: `?page=2&page_size=10`
  - **Always applied:** even with no pagination params, V2 applies `LIMIT 100 OFFSET 0` by default.
  - Defaults: `page=1`, `page_size=100`.

### Query Modifier Version Configuration

The active version is controlled via:
- `fr.set_query_modifier_version(fr.QueryModifierVersion.V2)` — set globally
- `fr.get_query_modifier_version()` — read the current global setting
- `fr.use_query_modifier_version(version)` — context manager; preferred for tests and per-request overrides

`fr.QueryModifierVersion` is an enum with two members: `QueryModifierVersion.V1` (default) and `QueryModifierVersion.V2`.

Views capture the active query-modifier version when they are registered with
`@fr.include_view(...)`. Set the global version before registering the view, or
set `query_modifier_version = fr.QueryModifierVersion.V2` on the view class directly
for explicit per-view behavior.

`fr.create_query_param_schema(schema_cls)` is context-sensitive: it creates a V1 or V2
query-parameter schema depending on the currently active version.

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
class UserView(fr.AsyncAlchemyView):
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

### Schema Classes and Utilities

| Symbol | Description |
|---|---|
| `fr.BaseSchema` | Base Pydantic model with `from_attributes=True`. All schemas should inherit from this. |
| `fr.IDSchema[Model]` | Generic schema that serializes only the `id` of a related model. Used for FK inputs. |
| `fr.IDStampsSchema` | Combines `IDSchema` with read-only `created_at` / `updated_at` fields. |
| `fr.TimestampsSchemaMixin` | Pydantic mixin adding read-only `created_at` / `updated_at` fields to a schema. |
| `fr.ReadOnly[T]` | Type annotation marker. Fields annotated `ReadOnly[T]` are excluded from create/update inputs. |
| `fr.WriteOnly[T]` | Type annotation marker. Fields annotated `WriteOnly[T]` are excluded from responses. |
| `fr.OmitReadOnlyMixin` | Mixin that strips `ReadOnly` fields from a schema subclass (used by `creation_schema`). |
| `fr.PatchMixin` | Mixin that makes all writable fields optional with `None` default (used by `update_schema`). |
| `fr.create_schema_from_model(model)` | Auto-generate a Pydantic schema from a SQLAlchemy model. |

### View Class Attributes

| Attribute | Type | Description |
|---|---|---|
| `schema` | `ClassVar[type[BaseSchema]]` | The primary Pydantic schema for responses. If omitted, auto-generated from `model`. |
| `creation_schema` | `ClassVar[type[BaseSchema]]` | Schema for `POST` input. Auto-derived by removing `ReadOnly` fields. |
| `update_schema` | `ClassVar[type[BaseSchema]]` | Schema for `PATCH` input. Auto-derived by making all writable fields optional. |
| `model` | `ClassVar[type[DeclarativeBase]]` | The SQLAlchemy model class. |
| `id_type` | `ClassVar[type]` | Primary key type used in `GET /{id}` and `DELETE /{id}`. Defaults to `int`. |
| `include_pagination_metadata` | `ClassVar[bool]` | Set `True` to return the paginated metadata envelope. Defaults to `False`. |
| `exclude_routes` | `ClassVar[tuple[str, ...]]` | Route names to suppress. |
| `query_modifier_version` | `ClassVar[QueryModifierVersion]` | Per-view query style override. Defaults to the global setting at registration time. |

### Database

| Symbol | Description |
|---|---|
| `fr.FRAsyncSession` | Global async session proxy. Use as a FastAPI dependency or call directly in tests. |
| `fr.FRSession` | Global sync session proxy. |
| `fr.AsyncSession` | Deprecated alias for `FRAsyncSession`. |
| `fr.Session` | Deprecated alias for `FRSession`. |
| `fr.AsyncSessionDep` | FastAPI `Depends`-compatible async session dependency. |
| `fr.SessionDep` | FastAPI `Depends`-compatible sync session dependency. |
| `fr.setup_async_database_connection(async_engine)` | Configure the framework with an async SQLAlchemy engine. |
| `fr.setup_database_connection(engine)` | Configure the framework with a sync SQLAlchemy engine. |
| `fr.activate_savepoint_only_mode()` | Wrap the session in a savepoint so test data never commits. |
| `fr.deactivate_savepoint_only_mode()` | Restore normal session behavior. |
| `fr.use_fr_globals(globals_obj)` | Context manager that swaps the global state for test isolation. |
| `fr.get_fr_globals()` | Return the current `FRGlobals` instance (engine, session factory, etc.). |

### Settings

`fr.settings` is a `pydantic-settings` instance that reads from environment variables
prefixed with `FASTAPI_RESTLY_`:

| Setting | Env var | Default |
|---|---|---|
| `async_database_url` | `FASTAPI_RESTLY_ASYNC_DATABASE_URL` | `sqlite+aiosqlite:///:memory:` |
| `database_url` | `FASTAPI_RESTLY_DATABASE_URL` | `sqlite+pysqlite:///:memory:` |
| `session_generator` | — | `None` (use built-in generator) |
| `sync_session_generator` | — | `None` (use built-in generator) |

## Minimal Example

```python
import asyncio
import fastapi_restly as fr
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import Mapped

engine = create_async_engine("sqlite+aiosqlite:///app.db")
fr.setup_async_database_connection(async_engine=engine)
app = FastAPI()

class User(fr.IDBase):
    name: Mapped[str]

@fr.include_view(app)
class UserView(fr.AsyncAlchemyView):
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
