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
  └─ get()              ← HTTP contract (replace to change; see below)
       └─ on_get(id)    ← business-logic hook — override here
```

```
GET /
  └─ index()                        ← HTTP contract (replace to change; see below)
       └─ on_list(query_params)      ← business-logic hook — override here
       │    └─ build_list_query()    ← WHERE-clause seam shared with count_index
       └─ count_index(query_params)  ← only called when include_pagination_metadata = True
            └─ build_list_query()    ← same seam — overriding once filters list and total
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
            └─ save_object(obj)      ← low-level hook — override to add side effects

DELETE /{id}
  └─ delete()
       └─ on_delete(id)
            └─ on_get(id)
            └─ delete_object(obj)
```

**General rule:** prefer overriding `on_*` hooks for business logic and
`make_new_object` / `update_object` / `save_object` / `delete_object` for
lower-level structural changes. When you need to change the HTTP contract
itself — status code, response shape, headers, or query parameter semantics —
replace the raw endpoint method; see
[Replace a generated route](#replace-a-generated-route) below.

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
current user. The single seam for this is `build_list_query`, which both
`on_list` and `count_index` consult — override it once and pagination
totals stay aligned with the listed rows.

```python
import sqlalchemy as sa

@fr.include_view(app)
class DocumentView(fr.AsyncRestView):
    prefix = "/documents"
    model = Document
    schema = DocumentSchema
    include_pagination_metadata = True

    def build_list_query(self):
        user_id = self.request.state.user_id
        return super().build_list_query().where(Document.owner_id == user_id)
```

Calling `super().build_list_query()` and chaining `.where(...)` composes
cleanly with any base-class or mixin filter. For multi-tenant scoping,
soft-delete hiding, or permission-based visibility, this is the seam to
reach for.

If you need `on_list` to also do work *beyond* a `WHERE` clause —
post-query result decoration, pre-query joins, eager-loading tweaks —
override `on_list` itself and use its optional `query` argument:

```python
    async def on_list(self, query_params, query=None):
        objs = await super().on_list(query_params, query)
        for obj in objs:
            obj._display_name = derive_display_name(obj)
        return objs
```

`on_list` accepts an optional `query` argument — a SQLAlchemy `Select`
statement. Pass it to restrict the result set before query modifiers
(filters, sorting, pagination) are applied on top. A `query` passed this
way is *not* shared with `count_index`, so reach for `build_list_query`
instead whenever the filter should also apply to pagination totals.

---

## Override low-level object hooks

When the change you need applies to *all* writes (both create and update), it
is cleaner to override the low-level hooks rather than duplicate logic in
`on_create` and `on_update`.

Two rules apply to `make_new_object` / `update_object` overrides:

- **Per-view application logic** (password hashing, slug derivation,
  status-transition events) belongs in `on_create` / `on_update`,
  written from scratch using the
  [CRUD utility helpers](api_reference.md#crud-utility-free-functions).
  Layering it through `make_new_object` creates ordering surprises and
  hides where the create flow lives.
- **Structural cross-cutting concerns** that only stamp server-controlled
  fields (audit IDs, tenant IDs, soft-delete timestamps) *are* the right
  fit for these hooks, layered through mixins.
  See [Composing views with mixins](howto_compose_views_with_mixins.md)
  for the pattern, the discriminator between the two rules, and the
  three reusable mixins from the SaaS example.

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

## Replace a generated route

The `on_*` hooks let you change what happens *inside* a generated route while
leaving its HTTP contract intact. Sometimes you need more than that: a
different response shape, custom response headers, a non-standard status code,
or completely different query parameter semantics. In those cases you can
replace the generated route itself.

To replace a route, define a method with the same name as the generated route
and add a route decorator to it:

```python
@fr.include_view(app)
class OrderView(fr.AsyncRestView):
    prefix = "/orders"
    model = Order
    schema = OrderSchema

    @fr.delete("/{id}", status_code=200)
    async def delete(self, id: int):
        obj = await self.on_get(id)
        serialized = self.to_response_schema(obj).model_dump(mode="json")
        await self.delete_object(obj)
        return serialized
```

The route decorator is required. When the framework initialises a view it
checks whether each standard route is already defined directly on the class.
If it finds `delete` with a route decorator, it uses that version and skips
the one from `AsyncRestView`. All other generated routes (`GET /`,
`GET /{id}`, `POST /`, `PATCH /{id}`) remain unchanged.

### Route replacement vs hook override

These two are easy to conflate:

| Technique | How | When to use |
|---|---|---|
| Override an `on_*` hook | `async def on_create(self, ...)` — no decorator | Change business logic; keep the HTTP contract |
| Replace a route | `@fr.delete("/{id}") async def delete(self, ...)` — with decorator | Change the HTTP contract: status code, response shape, headers, query params |

Use hooks for the common case. Route replacement is for the cases where you
genuinely need to renegotiate what the endpoint looks like on the wire.

### What remains available inside a replacement

A replacement is a full view method. Everything the parent view provides is
still on `self`:

- `self.session`, `self.request`, `self.model`, `self.schema`
- All `on_*` hooks — call them to reuse existing business logic without
  re-implementing it
- `self.to_response_schema(obj)` — serialise an ORM object to the configured
  Pydantic schema
- `self.make_new_object`, `self.update_object`, `self.save_object`,
  `self.delete_object`

The example above delegates the 404 check to `self.on_get(id)` and the
database removal to `self.delete_object(obj)`. Only the HTTP response layer
changes.

### Example: return the deleted record

The default `DELETE /{id}` returns `204 No Content`. Some API contracts
(for instance `ra-data-simple-rest` for react-admin) expect the deleted record
back as JSON:

```python
@fr.include_view(app)
class ProductView(fr.AsyncRestView):
    prefix = "/products"
    model = Product
    schema = ProductSchema

    @fr.delete("/{id}", status_code=200)
    async def delete(self, id: int):
        obj = await self.on_get(id)
        serialized = self.to_response_schema(obj).model_dump(mode="json")
        await self.delete_object(obj)
        return serialized
```

The four other generated routes are unaffected.

### Example: replace the list endpoint

Replace `index` to take full control of how the list is returned — for
instance to add custom response headers. Note that the replacement takes no
`query_params` argument; the framework's automatic query parameter injection
only applies to the standard generated `index`. Read query parameters directly
from `self.request.query_params` if you need them:

```python
import fastapi
import json

@fr.include_view(app)
class ProductView(fr.AsyncRestView):
    prefix = "/products"
    model = Product
    schema = ProductSchema

    @fr.get("/")
    async def index(self):
        items = await self.on_list({})
        total = await self.count_index({})
        serialized = [
            self.to_response_schema(obj).model_dump(mode="json") for obj in items
        ]
        return fastapi.Response(
            content=json.dumps(serialized),
            media_type="application/json",
            headers={"X-Total-Count": str(total)},
        )
```

### Share a replacement across views with a mixin

If several views need the same changed contract, put the replacement in a
mixin. Python's method resolution order ensures the mixin's version is picked
up before the standard one:

```python
class DeleteReturnsObjectMixin:
    @fr.delete("/{id}", status_code=200)
    async def delete(self, id):
        obj = await self.on_get(id)
        serialized = self.to_response_schema(obj).model_dump(mode="json")
        await self.delete_object(obj)
        return serialized


@fr.include_view(app)
class ProductView(DeleteReturnsObjectMixin, fr.AsyncRestView):
    prefix = "/products"
    model = Product
    schema = ProductSchema


@fr.include_view(app)
class OrderView(DeleteReturnsObjectMixin, fr.AsyncRestView):
    prefix = "/orders"
    model = Order
    schema = OrderSchema
```

Both views now return the deleted record as JSON. All other generated routes
behave normally on both.

This mixin pattern is also how `fr.AsyncReactAdminView` and `fr.ReactAdminView`
are implemented internally: `ReactAdminMixin` replaces `index` with one that
speaks the `ra-data-simple-rest` wire contract, and the two concrete view
classes are thin subclasses that add only the async/sync distinction.

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
