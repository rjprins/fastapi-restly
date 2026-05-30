# How-To: Override CRUD Behavior and Add Custom Endpoints

FastAPI-Restly generates five standard CRUD endpoints for every view, but real applications always need to bend the rules: inject extra fields, restrict which rows a user may see, run side effects, or expose non-CRUD operations. This guide walks through the override system from the highest-level route shell down to raw session access.

Every CRUD verb is structured as **three tiers**. Picking the right tier is the whole skill: most overrides belong in the lowest one. This page shows what to override where. For the full conceptual model — why the tiers are split this way and how the commit bracket works — read [The Handle Design](the_handle_design.md). The API reference also classifies the full view method surface: [View Method Surface](api_reference.md#view-method-surface).

Every concrete view class must be registered with `fr.include_view(app, ViewClass)` or the decorator shortcut before FastAPI sees its routes. Larger apps are easier to organize when view modules define classes and app/router modules include them.

---

## The three tiers of a CRUD verb

Take any verb — `create`, say. It is implemented as three methods, each at a different altitude:

| Tier | Methods | Owns | Override to… |
|---|---|---|---|
| **1. Route shell** (wire) | `create_endpoint`, `get_many_endpoint`, `get_one_endpoint`, `update_endpoint`, `delete_endpoint` | The `@route`, the FastAPI signature, `response_model`, and `to_response` | Change the **HTTP contract** (status code, response shape, headers) |
| **2. Request handler** | `handle_create`, `handle_get_many`, `handle_get_one`, `handle_update`, `handle_delete` | `authorize` and the commit bracket (`before_commit` → commit → `after_commit`); returns the domain object | Change **orchestration / timing** (custom transaction, async delete) without re-declaring the route |
| **3. Business verb** (domain) | `create`, `get_many`, `get_one`, `update`, `delete` | The domain operation: build / apply / save. **Auth-free and commit-free.** | Change **domain logic** (hash a password, derive a slug, compute a field) — the usual override point |

The call chain for `POST /` is:

```
POST /
  └─ create_endpoint(schema_obj)        ← tier 1: route shell (wire)
       └─ handle_create(schema_obj)      ← tier 2: authorize + commit bracket
            ├─ authorize("create", data=schema_obj)
            ├─ create(schema_obj)        ← tier 3: domain op — the usual override point
            │    ├─ make_new_object(schema_obj)
            │    └─ save_object(obj)
            ├─ before_commit("create", new=obj)
            ├─ commit            ← the framework owns the commit
            └─ after_commit("create", new=obj)
```

The same shape holds for every verb. `GET /{id}` is the simplest:

```
GET /{id}
  └─ get_one_endpoint(id)               ← tier 1: route shell
       └─ handle_get_one(id)            ← tier 2: scoped load + read-auth
            ├─ get_one(id)              ← tier 3: load via build_query (404 by visibility)
            └─ authorize("get_one", obj=obj)
```

`GET /` (list) routes its domain op through three read methods:

```
GET /
  └─ get_many_endpoint(query_params)    ← tier 1: route shell
       └─ handle_get_many(query_params) ← tier 2: authorize + get_many
            ├─ authorize("get_many")
            └─ get_many(query_params)   ← tier 3: domain read
                 ├─ build_query()       ← read scope (shared with get_one)
                 ├─ apply_query_params(query, query_params)
                 └─ count(query)
```

**The default rule:** override the **business verb** (tier 3) for domain logic. Reach up to **`handle_<verb>`** (tier 2) only when you need to change orchestration or the transaction, and to the **route shell** (tier 1) only when you need to renegotiate the HTTP contract on the wire.

### Why commit-free domain verbs matter

The framework owns the `commit` inside `handle_<verb>`, *after* the business verb returns. So `after_commit` runs after the write is durable, and — crucially — the business verb never commits. That kills the old "mutate-after-save" trap: you can build an object, set a derived field, save it, and return it, all in `create`, and it persists correctly because the commit happens later.

```python
async def create(self, schema_obj):
    obj = await self.make_new_object(schema_obj)
    obj.password_hash = hash_password(schema_obj.password)
    return await self.save_object(obj)
```

---

## Tier 3: override the business verb (the common case)

Each business verb maps to one domain operation. Override the one you want to change; the others keep their defaults. These methods are **auth-free and commit-free** — `handle_<verb>` adds the `authorize` call and owns the commit around them, so you only write domain logic.

### `create` — inject server-side fields at creation

```python
import fastapi_restly as fr

@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserRead

    async def create(self, schema_obj):
        obj = await self.make_new_object(schema_obj)
        obj.created_by = self.request.state.user_id  # set from request context
        return await self.save_object(obj)
```

`self.request` is the live FastAPI `Request`. `self.session` is the injected async SQLAlchemy session. Both are available in every method.

### `update` — run validation before saving

`update` receives the already-loaded object (fetched and visibility-scoped by `handle_update`), not the id:

```python
    async def update(self, obj, schema_obj):
        if obj.locked:
            raise fastapi.HTTPException(409, "Cannot update a locked record")
        obj = await self.update_object(obj, schema_obj)
        return await self.save_object(obj)
```

### `delete` — soft-delete instead of removing the row

`delete` also receives the loaded object. Flip a timestamp instead of deleting:

```python
    async def delete(self, obj):
        obj.deleted_at = datetime.utcnow()
        await self.session.flush()
        # Do NOT call super() — that would remove the row.
```

(For a reusable soft-delete that also hides the rows on read, see the `SoftDeleteMixin` in [Composing views with mixins](howto_compose_views_with_mixins.md).)

### `get_one` — eager-load extra relationships

The default `get_one` loads through `build_query` and the view's schema-derived loader options. If you need something extra just for one endpoint, override and load it manually — but keep routing through `build_query` so visibility scoping still applies:

```python
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import selectinload

    async def get_one(self, id):
        pk = sa_inspect(self.model).primary_key[0]
        query = self.build_query().where(pk == id).options(
            selectinload(User.audit_log)
        )
        obj = (await self.session.scalars(query)).first()
        if obj is None:
            raise fr.NotFound(f"User {id!r} not found")
        return obj
```

### `get_many` — decorate results after the query

When you need work *beyond* a `WHERE` clause — post-query result decoration or response-side annotation — override `get_many` and delegate to `super()`. (For base filters/joins/eager-loading, prefer `build_query` instead; see below.)

```python
    async def get_many(self, query_params):
        result = await super().get_many(query_params)
        for obj in result.objects:
            obj._display_name = derive_display_name(obj)
        return result
```

---

## Read scope: `build_query` + `authorize`

Read access is two independent concerns:

- **Visibility** — which rows exist at all for this caller — lives in `build_query`.
- **Policy** — whether this caller may perform the action — lives in `authorize`, called by the handler.

### `build_query` — scope every read at once

The single override point for read scoping is `build_query`, which `get_many` (list + count) and `get_one` (retrieve) both route through. Override it once and tenant scoping, soft-delete hiding, or row-level visibility apply uniformly:

- the listed page,
- the pagination total (`count` counts the same scoped query),
- and single-row fetches — a row hidden from the list returns **404** from `GET /{id}` as well, with no extra code.

Because `handle_update` and `handle_delete` load through `get_one` first, they inherit the same visibility check.

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

Calling `super().build_query()` and chaining `.where(...)` composes cleanly with any base-class or mixin filter. `build_query` is also the place for joins, eager-loading `.options(...)`, and other `Select` reshaping that should apply to every read. That keeps listing, pagination totals, and single-row fetches aligned by construction.

Note `get_one` stays **auth-free** even though it 404s on hidden rows: visibility is structural (the row isn't in the query), so every caller — including custom action routes that call `get_one(id)` — gets the same scoping for free.

### `authorize` — gate the action

`authorize(action, obj=None, data=None)` runs inside `handle_<verb>` at the right phase: before the write for `create`, and *after* the object is loaded for `get_one` / `update` / `delete`, so you can make row-level decisions on `obj`. The default consults a declarative `permissions` dict:

```python
@fr.include_view(app)
class InvoiceView(fr.AsyncRestView):
    prefix = "/invoices"
    model = Invoice
    schema = InvoiceRead

    permissions = {
        "create": "invoice:write",
        "update": "invoice:write",
        "delete": "invoice:admin",
    }
```

Each value is a permission string; the default `authorize` calls `self.request.user.has_permission(perm)` and raises `fr.Forbidden` on failure. Override `authorize` for row-level or data-aware checks:

```python
    async def authorize(self, action, obj=None, data=None):
        await super().authorize(action, obj=obj, data=data)  # keep the permissions check
        if action == "update" and obj.posted:
            raise fr.Forbidden("Posted invoices are immutable")
```

Visibility belongs in `build_query`, not here — raising from `authorize` produces a 403, whereas hiding a row through `build_query` produces a 404.

---

## Tier 2: override `handle_<verb>` for orchestration

Reach for `handle_<verb>` when you need to change *how* a verb is orchestrated — the transaction, the timing of side effects, the order of authorize-and-load — **without** re-declaring the route. The handler is where `authorize` and the commit bracket live.

A common case: stamp server-controlled fields cooperatively. Prefer `prepare_create` / `prepare_update` (below) for that. Use a full `handle_<verb>` override when the *bracket itself* must change — for example, deleting through a background job so the HTTP response returns before the row is gone:

```python
    async def handle_delete(self, id):
        obj = await self.get_one(id)
        await self.authorize("delete", obj=obj)
        obj.status = "pending_deletion"
        await self.save_object(obj)
        await self._commit()
        await enqueue_async_delete(obj.id)  # actual delete happens off-request
```

Because you call the tier-3 verbs (`get_one`) and utilities (`save_object`, `_commit`) yourself, you keep full control of orchestration while the route shell and response stay untouched.

### Transaction hooks: `before_commit` / `after_commit`

For most timing needs you do **not** need to override the handler — the bracket exposes two hooks the framework calls for you:

- `before_commit(action, new, old=None)` — runs inside the transaction, committed atomically with the write. Use it for outbox rows or audit rows.
- `after_commit(action, new, old=None)` — runs after the write is durable. Use it for email, webhooks, or cache invalidation.

`old` is a snapshot dict of the object's column values before the mutation (see `snapshot`), which enables dirty detection:

```python
    async def after_commit(self, action, new, old=None):
        if action == "update" and old["status"] != new.status:
            await notify_status_change(new.id, new.status)
```

### Cooperative field stamping: `prepare_create` / `prepare_update`

When the only thing you need is to stamp extra server-controlled fields (audit ids, tenant id, ownership), return them as a dict from `prepare_create` / `prepare_update`. `make_new_object` / `update_object` apply them. These layer cooperatively through mixins, which is exactly what structural concerns want:

```python
    async def prepare_create(self, schema_obj):
        fields = await super().prepare_create(schema_obj)
        fields["tenant_id"] = self.request.state.tenant_id
        return fields
```

See [Composing views with mixins](howto_compose_views_with_mixins.md) for the discriminator between this (structural stamping → mixin) and per-view business logic (compute a value from schema inputs → override the business verb).

---

## Domain utilities — call, don't override

The business verbs are built from a handful of low-level utilities. **Call** them from your `create` / `update` / `delete`; they are not the override point.

| Method | What it does |
|---|---|
| `self.make_new_object(schema_obj)` | Construct a new ORM object from the schema and add it to the session (runs `prepare_create`). **Does not flush.** |
| `self.update_object(obj, schema_obj)` | Apply writable fields onto an existing object (runs `prepare_update`). **Does not flush.** |
| `self.save_object(obj)` | Flush and refresh `obj` from the database. **Does not commit.** |
| `self.delete_object(obj)` | Remove `obj` and flush. **Does not commit.** |

The same operations are available as free functions for use outside a view — scripts, workers, services: `fr.objects.async_make_new_object`, `async_update_object`, `async_save_object`, `async_delete_object` (and their sync counterparts). See [Advanced Object Helpers](api_reference.md#advanced-object-helpers).

```python
from fastapi_restly.objects import async_make_new_object, async_save_object


async def import_user(session, payload) -> User:
    user = await async_make_new_object(session, User, payload, UserRead)
    user.password_hash = hash_password(payload.password)
    await async_save_object(session, user)
    await session.commit()
    return user
```

Because none of these commit, the same `create`/`update` code works identically inside a view (the handler commits) and inside a worker (you commit).

---

## Tier 1: replace a route shell to change the HTTP contract

The business verbs and handlers let you change what happens *inside* a generated route while leaving its wire contract intact. When you need a different response shape, custom headers, a non-standard status code, or different query-parameter semantics, replace the route shell itself.

To replace a route, define a method with the same name as the generated route shell (`get_many_endpoint`, `get_one_endpoint`, `create_endpoint`, `update_endpoint`, `delete_endpoint`) and add a route decorator. In most replacements you do **not** want to re-implement the commit bracket by hand — delegate to the handler, which already runs authorize, the bracket, and returns the domain object, then just reshape the response:

```python
@fr.include_view(app)
class ProductView(fr.AsyncRestView):
    prefix = "/products"
    model = Product
    schema = ProductRead

    @fr.delete("/{id}", status_code=200)
    async def delete_endpoint(self, id: int):
        obj = await self.get_one(id)               # load (scoped, 404)
        serialized = self.to_response_schema(obj).model_dump(mode="json")
        await self.handle_delete(id)               # authorize + delete + commit
        return serialized
```

When the framework initializes a view it checks whether each standard route shell is already defined directly on the class. If it finds `delete_endpoint` with a route decorator, it uses that version and skips the one from `AsyncRestView`. All other generated routes remain unchanged.

The default `DELETE /{id}` returns `204 No Content`. The version above returns the deleted record as JSON instead — useful for contracts like `ra-data-simple-rest` (react-admin) that expect the body back. The four other routes are unaffected.

### Route shell vs handler vs business verb

These are easy to conflate:

| Technique | How | When to use |
|---|---|---|
| Override a business verb | `async def create(self, schema_obj)` — no decorator | Change domain logic; keep auth, commit, and HTTP contract |
| Override `handle_<verb>` | `async def handle_create(self, schema_obj)` — no decorator | Change orchestration / transaction; keep the HTTP contract |
| Replace a route shell | `@fr.delete("/{id}") async def delete_endpoint(self, ...)` — with decorator | Change the HTTP contract: status code, response shape, headers, query params |

Use the business verb for the common case. Climb a tier only when the change genuinely belongs at that altitude.

### `to_response` — the one response method

Generated route shells return their result through `self.to_response(obj_or_list, action)`. This is the single wire-side response method: it produces the `204` on delete, the list envelope on `get_many`, and the validated response schema otherwise. Override it for envelopes, per-action projection, or a custom status code (return a `fastapi.Response`) without replacing each route shell:

```python
    def to_response(self, obj_or_list, action):
        if action == "create":
            return fastapi.Response(
                content=self.to_response_schema(obj_or_list).model_dump_json(),
                media_type="application/json",
                status_code=201,
                headers={"Location": f"{self.prefix}/{obj_or_list.id}"},
            )
        return super().to_response(obj_or_list, action)
```

For the per-object serialization itself, `to_response_schema(obj)` builds a response payload from the configured schema, strips `WriteOnly` fields, normalizes relationship id fields, and validates through Pydantic — so response-side `@field_validator` / `@field_serializer` hooks behave exactly as they would on an ordinary Pydantic model. Override `to_response_schema` when one endpoint family needs a different projection, or a faster path that skips validation:

```python
    def to_response_schema(self, obj: User) -> UserRead:
        return self.schema.model_construct(
            id=obj.id,
            name=obj.name,
            email=obj.email,
        )
```

`model_construct()` is an escape hatch: it bypasses validators and required-field checks. Keep the payload aligned with your public response contract, and never include `WriteOnly` fields such as passwords or API tokens.

### Replace the list route shell

Replace `get_many_endpoint` to take full control of how the list is returned — for instance to add custom response headers. The replacement takes no `query_params` argument; the framework's automatic query-parameter injection only applies to the standard generated shell. Read query parameters from `self.request.query_params` if you need them:

```python
import fastapi
import json

@fr.include_view(app)
class ProductView(fr.AsyncRestView):
    prefix = "/products"
    model = Product
    schema = ProductRead

    @fr.get("/")
    async def get_many_endpoint(self):
        result = await self.handle_get_many({})
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

If several views need the same changed contract, put the replacement in a mixin. Python's MRO ensures the mixin's version is picked up before the standard one:

```python
class DeleteReturnsObjectMixin:
    @fr.delete("/{id}", status_code=200)
    async def delete_endpoint(self, id):
        obj = await self.get_one(id)
        serialized = self.to_response_schema(obj).model_dump(mode="json")
        await self.handle_delete(id)
        return serialized


@fr.include_view(app)
class ProductView(DeleteReturnsObjectMixin, fr.AsyncRestView):
    prefix = "/products"
    model = Product
    schema = ProductRead
```

The public React Admin views use the same route-shell-replacement pattern internally: `fr.AsyncReactAdminView` and `fr.ReactAdminView` replace `get_many_endpoint` with one that speaks the `ra-data-simple-rest` wire contract, while preserving the standard CRUD verbs and handlers for the rest of the view.

---

## Add a custom read route

Use `@fr.get` to expose computed or summarised data alongside the generated endpoints. Call `get_one(id)` (the auth-free load) or `handle_get_one(id)` (load + read-auth) to reuse the view's scoping and 404 behavior:

```python
@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserRead

    @fr.get("/{id}/summary")
    async def summary(self, id: int):
        user = await self.handle_get_one(id)   # scoped load + read-auth + 404
        return {
            "id": user.id,
            "display_name": f"{user.first_name} {user.last_name}",
            "email": user.email,
        }
```

`get_one` / `handle_get_one` return the raw ORM object, so you can access all model attributes directly.

---

## Add a custom action route

Use `@fr.post` (or `@fr.patch`, `@fr.delete`) for explicit state-change actions such as archive, publish, or recalculate. A custom action has two good shapes:

**Compose the domain verbs and reuse a handler's bracket.** Load with `get_one(id)`, mutate, and let `handle_update` (or `handle_create`) run the authorize-and-commit bracket so durability and post-commit hooks behave like a normal write:

```python
@fr.include_view(app)
class OrderView(fr.AsyncRestView):
    prefix = "/orders"
    model = Order
    schema = OrderRead

    @fr.post("/{id}/archive", status_code=202)
    async def archive(self, id: int):
        order = await self.get_one(id)
        if order.archived:
            raise fastapi.HTTPException(409, "Already archived")
        order.archived = True
        await self.save_object(order)
        await self._commit()
        return {"id": order.id, "archived": order.archived}
```

**Run a full create/update through a handler.** When an action *is* a create or update under a different URL, build the input schema and call `handle_create` / `handle_update` to get authorize + the commit bracket for free:

```python
    @fr.post("/{id}/duplicate", status_code=201)
    async def duplicate(self, id: int):
        original = await self.get_one(id)
        payload = self.schema_create(name=f"{original.name} (copy)", ...)
        new_order = await self.handle_create(payload)
        return self.to_response_schema(new_order)
```

Reusing `handle_<verb>` from custom actions is the intended way to inherit the commit bracket — you get `before_commit` / commit / `after_commit` without re-declaring any of it.

### Relationship references in custom routes

Generated `POST` and `PATCH` routes validate the request body before Restly calls `make_new_object()` or `update_object()`, so `IDRef[Model]` fields are already `IDRef` instances by the time the resolver runs.

In a custom route, be careful when you construct a schema yourself. Pydantic's `model_construct()` skips validation, so scalar ids stay plain integers unless you wrap them explicitly:

```python
from fastapi_restly.objects import async_make_new_object


link_schema = TaskLabelRead.model_construct(
    task_id=fr.IDRef[Task](id=request.task_id),
    label_id=fr.IDRef[Label](id=label.id),
)

task_label = await async_make_new_object(
    self.session,
    TaskLabel,
    link_schema,
)
```

This keeps the resolver path active: Restly verifies the referenced rows exist and then writes the FK columns. It is especially useful when the schema inherits from `IDSchema` and validated construction would require response-only fields such as `id` or timestamps.

If you instead use `IDSchema[Model]` as a nested relationship-object field in a custom response schema, serialize the ORM object through `self.to_response_schema(obj)` before returning it:

```python
class TaskLabelNestedRead(fr.IDSchema):
    task: fr.IDSchema[Task]
    label: fr.IDSchema[Label]


@fr.post("/attach", response_model=TaskLabelNestedRead, status_code=201)
async def attach(self, request: AttachRequest):
    obj = await create_task_label(...)
    return self.to_response_schema(obj)
```

The raw ORM object usually has scalar FK columns, while the nested schema expects relationship-shaped data. `IDRef` fields do not need this extra step because their scalar wire format already matches the ORM FK value.

The SaaS example's `example-projects/saas/app/views/label.py` shows this in a `create_and_attach` route that creates a sibling row, flushes it to get an id, and then builds a second row with `IDRef` references.

---

## Raise HTTP errors from any method

Every method runs inside a request context, so you can raise `fastapi.HTTPException` (or `fr.Forbidden` / `fr.NotFound`) at any point:

```python
import fastapi

    async def create(self, schema_obj):
        if not self.request.state.user.is_admin:
            raise fastapi.HTTPException(403, "Admin access required")
        return await super().create(schema_obj)
```

For permission gating specifically, prefer `authorize` and the `permissions` dict (above) — it runs at the right phase of the handler and keeps the business verb auth-free.

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

Valid values are: `fr.ViewRoute.GET_MANY`, `fr.ViewRoute.GET_ONE`, `fr.ViewRoute.CREATE`, `fr.ViewRoute.UPDATE`, `fr.ViewRoute.DELETE`. Route-shell-name strings such as `"delete_endpoint"` are also accepted; any other string raises `AttributeError` at startup.

---

## Choosing between `@fr.route` and the shorthand decorators

Prefer `@fr.get`, `@fr.post`, `@fr.put`, `@fr.patch`, and `@fr.delete` for most endpoints. They set the HTTP method automatically and apply Restly's default status codes: `@fr.get`/`@fr.put`/`@fr.patch` use 200, `@fr.post` uses 201, and `@fr.delete` uses 204.

Use `@fr.route(path, methods=[...], ...)` only when you need full manual control over route options — for example, to register a single path under multiple HTTP methods, or to set a non-standard response code:

```python
    @fr.route("/{id}/thumbnail", methods=["GET", "HEAD"], status_code=200)
    async def thumbnail(self, id: int):
        ...
```

Both `@fr.route` and the shorthand decorators pass their keyword arguments through to FastAPI's route registration. Class-based routes therefore use the same configuration surface as regular FastAPI routes, including `response_model=`, `status_code=`, `dependencies=`, `responses=`, `tags=`, and other `APIRouter.add_api_route()` options.

---

## What is available on `self`

Inside any method or custom route, the following attributes are always available:

| Attribute | Type | Description |
|---|---|---|
| `self.session` | `AsyncSession` | The current database session |
| `self.request` | `fastapi.Request` | The live HTTP request |
| `self.model` | `type[DeclarativeBase]` | The SQLAlchemy model class |
| `self.schema` | `type[pydantic.BaseModel]` | The Pydantic response schema |

Any class-level `Annotated` dependency you declare on the view (e.g. a current user) is also injected and available as an instance attribute.

## See also

- [The Handle Design](the_handle_design.md) — the full three-tier model and the commit bracket.
- [Composing views with mixins](howto_compose_views_with_mixins.md) — structural stamping and scoping through cooperative mixins.
- [View Method Surface](api_reference.md#view-method-surface) — the complete classified method list.
