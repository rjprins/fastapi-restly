# How-To: Override CRUD Behavior and Add Custom Endpoints

FastAPI-Restly generates five standard CRUD endpoints for every view, but real
applications always need to bend the rules: inject extra fields, restrict which
rows a user may see, run side effects, or expose non-CRUD operations. This guide
walks through every layer of the override system, from the highest-level handlers
down to raw session access.

Every concrete view class must be registered with `fr.include_view(app, ViewClass)`
or the decorator shortcut before FastAPI sees its routes. Larger apps are easier
to organize when view modules define classes and app/router modules include them.

---

## How the handler chain works

Understanding the call chain helps you pick the right layer to override.
The API reference also classifies the full view method surface:
[View Method Surface](api_reference.md#view-method-surface).

### Read path

```
GET /{id}
  └─ get()         ← HTTP contract (replace to change; see below)
       └─ perform_get(id)    ← business-logic handler — override here
            └─ build_query()    ← same seam as listing — read filters apply here too
```

```
GET /
  └─ listing()                   ← HTTP contract (replace to change; see below)
       └─ perform_listing(query_params)      ← business-logic handler — override here
       │    └─ build_query()    ← WHERE-clause seam shared with retrieve
       │    └─ apply_list_params(query_params, query, ...)
       │    └─ count_listing(query)  ← counts the same query after stripping order/limit/offset
       └─ to_listing_response(query_params, result)  ← response-shape hook
            └─ to_paginated_listing_response(query_params, result)  ← paginated envelope hook
```

### Write path

```
POST /
  └─ create()                      ← FastAPI endpoint (avoid overriding)
       └─ perform_create(schema_obj)    ← operation handler — override here to add fields
            └─ make_new_object(...)  ← object helper — override to change construction
            └─ save_object(obj)      ← object helper — override to add side effects

PATCH /{id}
  └─ update()
       └─ perform_update(id, schema_obj)
            └─ perform_get(id)            ← reuses the same 404 logic
            └─ update_object(obj, schema_obj)
            └─ save_object(obj)      ← object helper — override to add side effects

DELETE /{id}
  └─ delete()
       └─ perform_delete(id)
            └─ perform_get(id)
            └─ delete_object(obj)
```

`make_new_object` and `update_object` prepare the ORM object but do not flush
the session. The default create and update handlers both call `save_object`
afterwards; that explicit save step is where the write is flushed and refreshed
from the database.

**General rule:** prefer overriding `perform_*` handlers for business logic and
`make_new_object` / `update_object` / `save_object` / `delete_object` for
lower-level structural changes. When you need to change the HTTP contract
itself — status code, response shape, headers, or query parameter semantics —
replace the raw endpoint method; see
[Replace a generated route](#replace-a-generated-route) below.

---

## Override a `perform_*` handler

Each `perform_*` handler maps one-to-one to a generated endpoint. Override the
one you want to change; the others keep their default implementations.

### `perform_create` — inject server-side fields at creation

```python
import fastapi_restly as fr

@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserRead

    async def perform_create(self, schema_obj):
        obj = await self.make_new_object(schema_obj)
        obj.created_by = self.request.state.user_id  # set from request context
        return await self.save_object(obj)
```

`self.request` is the live FastAPI `Request`. `self.session` is the injected
async SQLAlchemy session. Both are available in every handler.

### `perform_update` — run validation before saving

```python
    async def perform_update(self, id, schema_obj):
        obj = await self.perform_get(id)  # raises 404 if missing
        if obj.locked:
            raise fastapi.HTTPException(409, "Cannot update a locked record")
        obj = await self.update_object(obj, schema_obj)
        return await self.save_object(obj)
```

### `perform_delete` — require a confirmation header

```python
    async def perform_delete(self, id):
        if self.request.headers.get("X-Confirm-Delete") != "yes":
            raise fastapi.HTTPException(400, "Missing X-Confirm-Delete: yes header")
        return await super().perform_delete(id)
```

Using `super()` here keeps the existing 404-checking and deletion logic intact.

### `perform_get` — eager-load extra relationships

The default `perform_get` relies on the view's `schema` to decide which
relationships to load. If you need something extra just for one endpoint,
override and load it manually:

```python
from sqlalchemy.orm import selectinload

    async def perform_get(self, id):
        obj = await self.session.get(
            self.model, id,
            options=[selectinload(User.audit_log)]
        )
        if obj is None:
            raise fastapi.HTTPException(404)
        return obj
```

---

## Scope-filter reads

The most common real-world override: restrict reads to rows owned by the
current user. The single seam for this is `build_query`, which
`perform_listing` and `perform_get` consult. `count_listing` counts the query
built by `perform_listing`, so override it once and pagination totals stay
aligned with the listed rows, and a row hidden from listing returns 404
from `GET /{id}` as well.
`perform_update` and `perform_delete` inherit the visibility check via
`perform_get`.

```python
import sqlalchemy as sa

@fr.include_view(app)
class DocumentView(fr.AsyncRestView):
    prefix = "/documents"
    model = Document
    schema = DocumentRead
    include_pagination_metadata = True

    def build_query(self):
        user_id = self.request.state.user_id
        return super().build_query().where(Document.owner_id == user_id)
```

Calling `super().build_query()` and chaining `.where(...)` composes
cleanly with any base-class or mixin filter. For multi-tenant scoping,
soft-delete hiding, or row-level permission visibility, this is the seam
to reach for.

If you need `perform_listing` to also do work *beyond* a `WHERE` clause —
post-query result decoration or response-side annotation — override
`perform_listing` itself and delegate to `super()`:

```python
    async def perform_listing(self, query_params):
        result = await super().perform_listing(query_params)
        for obj in result.objects:
            obj._display_name = derive_display_name(obj)
        return result
```

If the listing needs a different SQL shape, prefer `build_query()` for base
filters, joins, eager-loading options, and other SQLAlchemy `Select` changes.
That keeps listing, pagination totals, and single-row fetches aligned.

---

## Override low-level object helpers

When the change you need applies to *all* writes (both create and update), it
is cleaner to override the low-level helpers rather than duplicate logic in
`perform_create` and `perform_update`.

Two rules apply to `make_new_object` / `update_object` overrides:

- **Per-view application logic** (password hashing, slug derivation,
  status-transition events) belongs in `perform_create` / `perform_update`,
  written from scratch using the
  [CRUD utility helpers](api_reference.md#crud-utility-free-functions).
  Layering it through `make_new_object` creates ordering surprises and
  hides where the create flow lives.
- **Structural cross-cutting concerns** that only stamp server-controlled
  fields (audit IDs, tenant IDs, soft-delete timestamps) *are* the right
  fit for these helpers, layered through mixins.
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
than re-implementing the handler from scratch. The pattern is consistent across all
handlers:

```python
    async def perform_listing(self, query_params):
        result = await super().perform_listing(query_params)
        # Annotate each object with a computed field before serialization
        for obj in result.objects:
            obj._display_name = f"{obj.first_name} {obj.last_name}"
        return result
```

---

## Raise HTTP errors from handlers

All `perform_*` handlers run inside a request context, so you can raise
`fastapi.HTTPException` at any point:

```python
import fastapi

    async def perform_create(self, schema_obj):
        if not self.request.state.user.is_admin:
            raise fastapi.HTTPException(403, "Admin access required")
        return await super().perform_create(schema_obj)
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
    schema = UserRead

    @fr.get("/{id}/summary")
    async def summary(self, id: int):
        user = await self.perform_get(id)   # raises 404 automatically
        return {
            "id": user.id,
            "display_name": f"{user.first_name} {user.last_name}",
            "email": user.email,
        }
```

`perform_get` returns the raw ORM object, so you can access all model
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
    schema = OrderRead

    @fr.post("/{id}/archive", status_code=202)
    async def archive(self, id: int):
        order = await self.perform_get(id)
        if order.archived:
            raise fastapi.HTTPException(409, "Already archived")
        order.archived = True
        order = await self.save_object(order)
        return {"id": order.id, "archived": order.archived}
```

---

## Replace a generated route

The `perform_*` handlers let you change what happens *inside* a generated route while
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
    schema = OrderRead

    @fr.delete("/{id}", status_code=200)
    async def delete(self, id: int):
        obj = await self.perform_get(id)
        serialized = self.to_response_schema(obj).model_dump(mode="json")
        await self.delete_object(obj)
        return serialized
```

The route decorator is required. When the framework initialises a view it
checks whether each standard route is already defined directly on the class.
If it finds `delete` with a route decorator, it uses that version and skips
the one from `AsyncRestView`. All other generated routes (`GET /`,
`GET /{id}`, `POST /`, `PATCH /{id}`) remain unchanged.

### Route Replacement vs Handler Override

These two are easy to conflate:

| Technique | How | When to use |
|---|---|---|
| Override a `perform_*` handler | `async def perform_create(self, ...)` — no decorator | Change business logic; keep the HTTP contract |
| Replace a route | `@fr.delete("/{id}") async def delete(self, ...)` — with decorator | Change the HTTP contract: status code, response shape, headers, query params |

Use handlers for the common case. Route replacement is for the cases where you
genuinely need to renegotiate what the endpoint looks like on the wire.

### What remains available inside a replacement

A replacement is a full view method. Everything the parent view provides is
still on `self`:

- `self.session`, `self.request`, `self.model`, `self.schema`
- All `perform_*` handlers — call them to reuse existing business logic without
  re-implementing it
- `self.to_response_schema(obj)` — serialise an ORM object to the configured
  Pydantic schema
- `self.make_new_object`, `self.update_object`, `self.save_object`,
  `self.delete_object`

The example above delegates the 404 check to `self.perform_get(id)` and the
database removal to `self.delete_object(obj)`. Only the HTTP response layer
changes.

### Overriding response serialization

Generated routes call `self.to_response_schema(obj)` before returning ORM
objects. The default implementation builds a response payload from the configured
schema, strips `WriteOnly` fields, normalizes relationship id fields, and then
validates through Pydantic. That means response-side `@field_validator` and
`@field_serializer` hooks behave the same way they would on an ordinary
Pydantic response model.

Override `to_response_schema()` when one endpoint family needs a different
projection, or when you intentionally want a faster path that skips Pydantic
validation:

```python
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserRead

    def to_response_schema(self, obj: User) -> UserRead:
        return self.schema.model_construct(
            id=obj.id,
            name=obj.name,
            email=obj.email,
        )
```

`model_construct()` is an escape hatch: it bypasses validators and required-field
checks. Keep the payload aligned with your public response contract, and do not
include `WriteOnly` fields such as passwords or API tokens.

### Relationship references in custom routes

Generated `POST` and `PATCH` routes validate the request body before Restly
calls `make_new_object()` or `update_object()`, so `IDRef[Model]` fields are
already `IDRef` instances by the time the resolver runs.

In a custom route, be careful when you construct a schema yourself. Pydantic's
`model_construct()` skips validation, so scalar ids stay plain integers unless
you wrap them explicitly:

```python
link_schema = TaskLabelRead.model_construct(
    task_id=fr.IDRef[Task](id=request.task_id),
    label_id=fr.IDRef[Label](id=label.id),
)

task_label = await fr.async_make_new_object(
    self.session,
    TaskLabel,
    link_schema,
)
```

This keeps the resolver path active: Restly verifies the referenced rows exist
and then writes the FK columns. It is especially useful when the schema inherits
from `IDSchema` and validated construction would require response-only fields
such as `id` or timestamps. In that case, direct construction like
`TaskLabelRead(task_id=1, label_id=2)` would run the `IDRef` validators, but
it would also require those response-only values that the route does not have
yet.

If you instead use `IDSchema[Model]` as a nested relationship-object field in a
custom response schema, serialize the ORM object through `self.to_response_schema(obj)`
before returning it:

```python
class TaskLabelNestedRead(fr.IDSchema):
    task: fr.IDSchema[Task]
    label: fr.IDSchema[Label]


@fr.post("/attach", response_model=TaskLabelNestedRead, status_code=201)
async def attach(self, request: AttachRequest):
    obj = await create_task_label(...)
    return self.to_response_schema(obj)
```

The raw ORM object usually has scalar FK columns, while the nested schema expects
relationship-shaped data. `IDRef` fields do not need this extra step because
their scalar wire format already matches the ORM FK value.

The SaaS example's `example-projects/saas/app/views/label.py` shows this in a
`create_and_attach` route that creates a sibling row, flushes it to get an id,
and then builds a second row with `IDRef` references.

### Example: return the deleted record

The default `DELETE /{id}` returns `204 No Content`. Some API contracts
(for instance `ra-data-simple-rest` for react-admin) expect the deleted record
back as JSON:

```python
@fr.include_view(app)
class ProductView(fr.AsyncRestView):
    prefix = "/products"
    model = Product
    schema = ProductRead

    @fr.delete("/{id}", status_code=200)
    async def delete(self, id: int):
        obj = await self.perform_get(id)
        serialized = self.to_response_schema(obj).model_dump(mode="json")
        await self.delete_object(obj)
        return serialized
```

The four other generated routes are unaffected.

### Example: replace the listing endpoint

Replace `listing` to take full control of how the list is returned — for
instance to add custom response headers. Note that the replacement takes no
`query_params` argument; the framework's automatic query parameter injection
only applies to the standard generated `listing`. Read query parameters directly
from `self.request.query_params` if you need them:

```python
import fastapi
import json

@fr.include_view(app)
class ProductView(fr.AsyncRestView):
    prefix = "/products"
    model = Product
    schema = ProductRead

    @fr.get("/")
    async def listing(self):
        result = await self.perform_listing({})
        serialized = [
            self.to_response_schema(obj).model_dump(mode="json")
            for obj in result.objects
        ]
        return fastapi.Response(
            content=json.dumps(serialized),
            media_type="application/json",
            headers={"X-Total-Count": str(result.total_count)},
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
        obj = await self.perform_get(id)
        serialized = self.to_response_schema(obj).model_dump(mode="json")
        await self.delete_object(obj)
        return serialized


@fr.include_view(app)
class ProductView(DeleteReturnsObjectMixin, fr.AsyncRestView):
    prefix = "/products"
    model = Product
    schema = ProductRead


@fr.include_view(app)
class OrderView(DeleteReturnsObjectMixin, fr.AsyncRestView):
    prefix = "/orders"
    model = Order
    schema = OrderRead
```

Both views now return the deleted record as JSON. All other generated routes
behave normally on both.

The public React Admin views use the same route-replacement pattern
internally: `fr.AsyncReactAdminView` and `fr.ReactAdminView` replace `listing`
with one that speaks the `ra-data-simple-rest` wire contract, while preserving
the standard CRUD handlers for the rest of the view.

---

## Exclude generated routes

Set `exclude_routes` to suppress specific generated endpoints:

```python
@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    exclude_routes = [fr.ViewRoute.DELETE, fr.ViewRoute.UPDATE]
```

Valid values are: `fr.ViewRoute.LIST`, `fr.ViewRoute.GET`,
`fr.ViewRoute.CREATE`, `fr.ViewRoute.UPDATE`, `fr.ViewRoute.DELETE`. Route-name
strings such as `"delete"` are also accepted; any other
string raises `AttributeError` at startup.

---

## Choosing between `@fr.route` and the shorthand decorators

Prefer `@fr.get`, `@fr.post`, `@fr.put`, `@fr.patch`, and `@fr.delete` for
most endpoints. They set the HTTP method automatically and apply Restly's
default status codes: `@fr.get`/`@fr.put`/`@fr.patch` use 200, `@fr.post`
uses 201, and `@fr.delete` uses 204.

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

Inside any handler or custom route method, the following attributes are
always available:

| Attribute | Type | Description |
|---|---|---|
| `self.session` | `AsyncSession` | The current database session |
| `self.request` | `fastapi.Request` | The live HTTP request |
| `self.model` | `type[DeclarativeBase]` | The SQLAlchemy model class |
| `self.schema` | `type[pydantic.BaseModel]` | The Pydantic response schema |

Any class-level `Annotated` dependency you declare on the view (e.g. a current
user) is also injected and available as an instance attribute.
