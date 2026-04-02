# How-To: Override CRUD Behavior and Add Custom Endpoints

FastAPI-Restly generates five standard CRUD endpoints for every view, but real
applications always need to bend the rules: inject extra fields, restrict which
rows a user may see, run side effects, or expose non-CRUD operations. This guide
walks through every layer of the override system, from the highest-level hooks
down to raw session access.

Every concrete view class must be decorated with `@fr.include_view(app)` (or
an equivalent `APIRouter`), otherwise no routes are registered.

---

## How the hook chain works

Understanding the call chain helps you pick the right layer to override.

### Read path

```
GET /{id}
  └─ get()              ← FastAPI endpoint (avoid overriding)
       └─ on_get(id)    ← business-logic hook — override here
```

```
GET /
  └─ index()                       ← FastAPI endpoint (avoid overriding)
       └─ on_list(query_params)    ← business-logic hook — override here
       └─ count_index(query_params) ← only called when include_pagination_metadata = True
```

### Write path

```
POST /
  └─ post()                        ← FastAPI endpoint (avoid overriding)
       └─ on_create(schema_obj)    ← high-level hook — override here to add fields
            └─ make_new_object(...)  ← low-level hook — override to change construction
            └─ save_object(obj)      ← low-level hook — override to add side effects

PATCH /{id}
  └─ patch()
       └─ on_update(id, schema_obj)
            └─ on_get(id)            ← reuses the same 404 logic
            └─ update_object(obj, schema_obj)
                 └─ save_object(obj)

DELETE /{id}
  └─ delete()
       └─ on_delete(id)
            └─ on_get(id)
            └─ delete_object(obj)
```

**General rule:** prefer overriding `on_*` hooks for business logic and
`make_new_object` / `update_object` / `save_object` / `delete_object` for
lower-level structural changes. Override the raw endpoint methods (`get`,
`post`, etc.) only when you need to change the HTTP contract itself (status
code, response shape).

---

## Override an `on_*` hook

Each `on_*` hook maps one-to-one to a generated endpoint. Override the
one you want to change; the others keep their default implementations.

### `on_create` — inject server-side fields at creation

```python
import fastapi_restly as fr

@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserSchema

    async def on_create(self, schema_obj):
        obj = await self.make_new_object(schema_obj)
        obj.created_by = self.request.state.user_id  # set from request context
        return await self.save_object(obj)
```

`self.request` is the live FastAPI `Request`. `self.session` is the injected
async SQLAlchemy session. Both are available in every hook.

### `on_update` — run validation before saving

```python
    async def on_update(self, id, schema_obj):
        obj = await self.on_get(id)  # raises 404 if missing
        if obj.locked:
            raise fastapi.HTTPException(409, "Cannot update a locked record")
        return await self.update_object(obj, schema_obj)
```

### `on_delete` — require a confirmation header

```python
    async def on_delete(self, id):
        if self.request.headers.get("X-Confirm-Delete") != "yes":
            raise fastapi.HTTPException(400, "Missing X-Confirm-Delete: yes header")
        return await super().on_delete(id)
```

Using `super()` here keeps the existing 404-checking and deletion logic intact.

### `on_get` — eager-load extra relationships

The default `on_get` relies on the view's `schema` to decide which
relationships to load. If you need something extra just for one endpoint,
override and load it manually:

```python
from sqlalchemy.orm import selectinload

    async def on_get(self, id):
        obj = await self.session.get(
            self.model, id,
            options=[selectinload(User.audit_log)]
        )
        if obj is None:
            raise fastapi.HTTPException(404)
        return obj
```

---

## Scope-filter the list endpoint

The most common real-world override: restrict `GET /` to rows owned by the
current user. You need to override **both** `on_list` and `count_index`
so that pagination totals stay accurate.

```python
import sqlalchemy as sa

@fr.include_view(app)
class DocumentView(fr.AsyncRestView):
    prefix = "/documents"
    model = Document
    schema = DocumentSchema
    include_pagination_metadata = True

    def _owner_query(self):
        user_id = self.request.state.user_id
        return sa.select(Document).where(Document.owner_id == user_id)

    async def on_list(self, query_params, query=None):
        return await super().on_list(query_params, query=self._owner_query())

    async def count_index(self, query_params):
        from sqlalchemy import func, select
        filtered = self._owner_query()
        count_q = select(func.count()).select_from(filtered.subquery())
        return int(await self.session.scalar(count_q) or 0)
```

`on_list` accepts an optional `query` argument — a SQLAlchemy `Select`
statement. Pass it to restrict the result set before query modifiers (filters,
sorting, pagination) are applied on top.

---

## Override low-level object hooks

When the change you need applies to *all* writes (both create and update), it
is cleaner to override the low-level hooks rather than duplicate logic in
`on_create` and `on_update`.

### `save_object` — send a notification after every write

```python
    async def save_object(self, obj):
        obj = await super().save_object(obj)
        await notify_subscribers(obj.id)  # your async side-effect
        return obj
```

### `make_new_object` — set a default field on creation only

```python
    async def make_new_object(self, schema_obj):
        obj = await super().make_new_object(schema_obj)
        obj.tenant_id = self.request.state.tenant_id
        return obj
```

### `update_object` — prevent certain fields from being changed

```python
    async def update_object(self, obj, schema_obj):
        # Ignore any attempt to change `owner_id` via PATCH
        schema_obj.owner_id = None
        return await super().update_object(obj, schema_obj)
```

### `delete_object` — implement soft-delete

Instead of removing the row, mark it as archived:

```python
    async def delete_object(self, obj):
        obj.deleted_at = datetime.utcnow()
        await self.session.flush()
        # Do NOT call super() — that would remove the row.
```

---

## Extend rather than replace with `super()`

For most overrides, calling `super()` and tweaking the result is less error-prone
than re-implementing the hook from scratch. The pattern is consistent across all
hooks:

```python
    async def on_list(self, query_params, query=None):
        objs = await super().on_list(query_params, query)
        # Annotate each object with a computed field before serialization
        for obj in objs:
            obj._display_name = f"{obj.first_name} {obj.last_name}"
        return objs
```

---

## Raise HTTP errors from hooks

All `on_*` hooks run inside a request context, so you can raise
`fastapi.HTTPException` at any point:

```python
import fastapi

    async def on_create(self, schema_obj):
        if not self.request.state.user.is_admin:
            raise fastapi.HTTPException(403, "Admin access required")
        return await super().on_create(schema_obj)
```

---

## Add a custom read route

Use `@fr.get` to expose computed or summarised data alongside the generated
endpoints:

```python
@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserSchema

    @fr.get("/{id}/summary")
    async def summary(self, id: int):
        user = await self.on_get(id)   # raises 404 automatically
        return {
            "id": user.id,
            "display_name": f"{user.first_name} {user.last_name}",
            "email": user.email,
        }
```

`on_get` returns the raw ORM object, so you can access all model
attributes directly.

---

## Add a custom action route

Use `@fr.post` (or `@fr.patch`, `@fr.delete`) for explicit state-change
actions such as archive, publish, or recalculate:

```python
@fr.include_view(app)
class OrderView(fr.AsyncRestView):
    prefix = "/orders"
    model = Order
    schema = OrderSchema

    @fr.post("/{id}/archive", status_code=202)
    async def archive(self, id: int):
        order = await self.on_get(id)
        if order.archived:
            raise fastapi.HTTPException(409, "Already archived")
        order.archived = True
        order = await self.save_object(order)
        return {"id": order.id, "archived": order.archived}
```

---

## Exclude generated routes

Set `exclude_routes` to suppress specific generated endpoints:

```python
@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    exclude_routes = ("delete", "patch")
```

Valid values are: `"index"`, `"get"`, `"post"`, `"patch"`, `"delete"`. Any
other string raises `AttributeError` at startup.

---

## Choosing between `@fr.route` and the shorthand decorators

Prefer `@fr.get`, `@fr.post`, `@fr.put`, `@fr.patch`, and `@fr.delete` for
most endpoints — they set the HTTP method automatically, and `@fr.get` (200),
`@fr.post` (201), and `@fr.delete` (204) also set default status codes.
`@fr.put` and `@fr.patch` do not set a default; FastAPI uses 200.

Use `@fr.route(path, methods=[...], ...)` only when you need full manual
control over route options — for example, to register a single path under
multiple HTTP methods, or to set a non-standard response code:

```python
    @fr.route("/{id}/thumbnail", methods=["GET", "HEAD"], status_code=200)
    async def thumbnail(self, id: int):
        ...
```

---

## What is available on `self`

Inside any hook or custom route method, the following attributes are
always available:

| Attribute | Type | Description |
|---|---|---|
| `self.session` | `AsyncSession` | The current database session |
| `self.request` | `fastapi.Request` | The live HTTP request |
| `self.model` | `type[DeclarativeBase]` | The SQLAlchemy model class |
| `self.schema` | `type[BaseSchema]` | The Pydantic response schema |

Any class-level `Annotated` dependency you declare on the view (e.g. a current
user) is also injected and available as an instance attribute.
