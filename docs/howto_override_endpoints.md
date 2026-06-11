# Override CRUD Behavior and Add Custom Endpoints

FastAPI-Restly generates five CRUD endpoints per view. Real applications still need custom fields, row visibility, side effects, and non-CRUD actions. This guide shows which method to override.

Every CRUD verb has **three tiers**. Most overrides belong in the lowest tier. For the conceptual model, read [The Handle Design](the_handle_design.md). For the complete method list, see [View Method Surface](api_reference.md#view-method-surface).

Register each concrete view with `fr.include_view(app, ViewClass)` or the decorator shortcut. In larger apps, define view classes in view modules and include them from app/router modules.

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

**Default rule:** override the **business verb** for domain logic. Use **`handle_<verb>`** for orchestration or transaction changes. Replace the **route shell** only for HTTP contract changes.

### Why commit-free domain verbs matter

The handler commits *after* the business verb returns. That means `after_commit` runs after durability, and `create` / `update` / `delete` can build, mutate, save, and return without committing.

```python
async def create(self, schema_obj):
    obj = await self.make_new_object(schema_obj)
    obj.password_hash = hash_password(schema_obj.password)
    return await self.save_object(obj)
```

---

## Tier 3: override the business verb (the common case)

Each business verb maps to one domain operation. Override only the one you need. These methods are **auth-free and commit-free**; the handler adds authorization and commit handling.

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
        obj.deleted_at = datetime.now(timezone.utc)
        await self.session.flush()
        # Do NOT call super() — that would remove the row.
```

For reusable soft-delete that also hides rows on read, see `SoftDeleteMixin` in [Composing views with mixins](howto_compose_views_with_mixins.md).

### `get_one` — eager-load extra relationships

The default `get_one` loads through `build_query` and schema-derived loader options. If one endpoint needs extra eager loading, keep `build_query` in the query so visibility still applies:

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
            raise fr.exc.NotFound(f"User {id!r} not found")
        return obj
```

### `get_many` — decorate results after the query

For post-query decoration, override `get_many` and delegate to `super()`. For filters, joins, or eager loading that apply to every read, prefer `build_query`.

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

`build_query` is the read-scope override point. `get_many` (list + count) and `get_one` both use it, so one filter covers:

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

Calling `super().build_query()` and chaining `.where(...)` composes with base-class and mixin filters. Put joins, eager-loading `.options(...)`, and other read-wide `Select` changes here.

`get_one` stays **auth-free** even though it 404s on hidden rows: visibility comes from the query. Custom routes that call `get_one(id)` get the same scope.

### `authorize` — gate the action

`authorize(action, obj=None, data=None)` runs inside `handle_<verb>`: before create, and after the object is loaded for `get_one` / `update` / `delete`. Override it to enforce policy:

```python
@fr.include_view(app)
class InvoiceView(fr.AsyncRestView):
    prefix = "/invoices"
    model = Invoice
    schema = InvoiceRead

    async def authorize(self, action, obj=None, data=None):
        user = self.request.user  # populated by your auth middleware
        if action in ("create", "update", "delete") and not user.is_staff:
            raise fr.exc.Forbidden()
        if action == "update" and obj.posted:
            raise fr.exc.Forbidden("Posted invoices are immutable")
```

`action` is the verb, `obj` is the loaded row, and `data` is the validated request payload. Authentication itself is yours to wire; Restly calls `authorize` and maps `fr.exc.Forbidden` / `fr.exc.NotFound` to HTTP responses.

Visibility belongs in `build_query`, not here — raising from `authorize` produces a 403, whereas hiding a row through `build_query` produces a 404.

---

## Tier 2: override `handle_<verb>` for orchestration

Use `handle_<verb>` to change orchestration: transaction handling, side-effect timing, or authorize/load order. The handler owns `authorize` and the commit bracket.

For server-controlled field stamps, prefer `make_new_object` / `update_object` below. Use a handler override when the bracket itself must change:

```python
    async def handle_delete(self, id):
        obj = await self.get_one(id)
        # write_action runs the same bracket the default handle_delete uses:
        # authorize("delete", obj) -> snapshot -> body -> before/after_commit.
        async with self.write_action("delete", obj=obj):
            obj.status = "pending_deletion"
            await self.save_object(obj)
        await enqueue_async_delete(obj.id)  # actual delete happens off-request
```

Here the route shell stays untouched, while the handler controls the write bracket.

### Transaction hooks: `before_commit` / `after_commit`

For most timing needs, use the hooks instead of overriding the handler:

- `before_commit(action, new, old=None)` — runs inside the transaction, committed atomically with the write. Use it for outbox rows or audit rows.
- `after_commit(action, new, old=None)` — runs after the write is durable. Use it for email, webhooks, or cache invalidation.

`old` is a snapshot dict of the object's column values before the mutation (see `snapshot`), which enables dirty detection:

```python
    async def after_commit(self, action, new, old=None):
        if action == "update" and old["status"] != new.status:
            await notify_status_change(new.id, new.status)
```

### Cooperative field stamping: override `make_new_object` / `update_object`

For server-controlled field stamps, override `make_new_object` / `update_object` cooperatively: call `super()`, mutate, and return. This composes cleanly through mixins:

```python
    async def make_new_object(self, schema_obj):
        obj = await super().make_new_object(schema_obj)
        obj.tenant_id = self.request.state.tenant_id  # stamp the constructed object
        return obj
```

See [Composing views with mixins](howto_compose_views_with_mixins.md) for when to use structural stamping versus per-view business logic.

---

## Domain utilities — call, don't override

The business verbs are built from a handful of low-level utilities. **Call** them from your `create` / `update` / `delete`; they are not the override point.

| Method | What it does |
|---|---|
| `self.make_new_object(schema_obj)` | Construct a new ORM object from the schema and add it to the session (the cooperative override point for create-time field stamping). **Does not flush.** |
| `self.update_object(obj, schema_obj)` | Apply writable fields onto an existing object (the cooperative override point for update-time field stamping). **Does not flush.** |
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

Because none of these commit, the same code works inside a view or worker; only the caller owns the transaction.

---

## Tier 1: replace a route shell to change the HTTP contract

Business verbs and handlers change behavior inside a generated route. Replace the route shell for response shape, headers, status code, or query-parameter semantics.

To replace a route, define the same route-shell method name and add a route decorator. Usually, delegate to the handler and only reshape the response:

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

At view initialization, Restly uses route shells defined directly on the class and skips the matching generated shell. Other generated routes remain unchanged.

The default `DELETE /{id}` returns `204 No Content`; this version returns the deleted record, as `ra-data-simple-rest` expects.

### Route shell vs handler vs business verb

These are easy to conflate:

| Technique | How | When to use |
|---|---|---|
| Override a business verb | `async def create(self, schema_obj)` — no decorator | Change domain logic; keep auth, commit, and HTTP contract |
| Override `handle_<verb>` | `async def handle_create(self, schema_obj)` — no decorator | Change orchestration / transaction; keep the HTTP contract |
| Replace a route shell | `@fr.delete("/{id}") async def delete_endpoint(self, ...)` — with decorator | Change the HTTP contract: status code, response shape, headers, query params |

Use the business verb by default. Move up only when the higher tier owns the change.

### `to_response` — the one response method

Generated route shells return through `self.to_response(obj_or_list, shape)`, where `shape` is `SINGLE`, `LISTING`, or `EMPTY`. Override it for envelopes or shape-wide response behavior:

```python
    def to_response(self, obj_or_list, shape=fr.ResponseShape.SINGLE):
        if shape is fr.ResponseShape.SINGLE:
            return {"data": self.to_response_schema(obj_or_list)}
        return super().to_response(obj_or_list, shape)
```

`to_response` is keyed on wire shape, not action. It cannot distinguish `create` from `get_one`; both are `SINGLE`. For one verb's HTTP contract, override that route shell:

```python
    @fr.post("/")
    async def create_endpoint(self, schema_obj):
        obj = await self.handle_create(schema_obj)
        return fastapi.Response(
            content=self.to_response_schema(obj).model_dump_json(),
            media_type="application/json",
            status_code=201,
            headers={"Location": f"{self.prefix}/{obj.id}"},
        )
```

For object serialization, `to_response_schema(obj)` builds the configured schema, strips `WriteOnly` fields, normalizes relationship ids, and validates through Pydantic. Override it for a different projection or a faster trusted path:

```python
    def to_response_schema(self, obj: User) -> UserRead:
        return self.schema.model_construct(
            id=obj.id,
            name=obj.name,
            email=obj.email,
        )
```

`model_construct()` bypasses validators and required-field checks. Keep the payload aligned with your response contract, and never include `WriteOnly` fields.

### Replace the list route shell

Replace `get_many_endpoint` when the list response contract changes, for example custom headers. Automatic query-parameter injection only applies to the generated shell, so read `self.request.query_params` yourself:

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

React Admin views use this same pattern: they replace `get_many_endpoint` for the `ra-data-simple-rest` wire contract and keep the standard verbs and handlers.

---

## Add a custom read route

Use `@fr.get` for computed read endpoints. Call `get_one(id)` for scoped load + 404, or `handle_get_one(id)` to include read authorization:

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

Use `@fr.post` (or `@fr.patch`, `@fr.delete`) for state-change actions such as archive, publish, or recalculate. Use one of two shapes:

**Bracket the mutation with `write_action`.** Load with `handle_get_one(id)`, then use `self.write_action` under a custom action name:

```python
@fr.include_view(app)
class OrderView(fr.AsyncRestView):
    prefix = "/orders"
    model = Order
    schema = OrderRead

    @fr.post("/{id}/archive", status_code=202)
    async def archive(self, id: int):
        order = await self.handle_get_one(id)
        if order.archived:
            raise fastapi.HTTPException(409, "Already archived")
        async with self.write_action("archive", obj=order):
            order.archived = True
        return {"id": order.id, "archived": order.archived}
```

**Run a full create/update through a handler.** If an action is create or update under another URL, build the input schema and call `handle_create` / `handle_update`:

```python
    @fr.post("/{id}/duplicate", status_code=201)
    async def duplicate(self, id: int):
        original = await self.get_one(id)
        payload = self.schema_create(name=f"{original.name} (copy)", ...)
        new_order = await self.handle_create(payload)
        return self.to_response_schema(new_order)
```

Reusing `handle_<verb>` inherits authorization and the commit bracket.

### Relationship references in custom routes

Generated `POST` and `PATCH` routes validate the body before Restly calls `make_new_object()` or `update_object()`, so `IDRef[Model]` fields are already `IDRef` instances.

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

This keeps the resolver path active: Restly verifies referenced rows and writes the FK columns. It helps when validated construction would require response-only fields such as `id` or timestamps.

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

The raw ORM object usually has scalar FK columns, while a nested schema expects relationship-shaped data. `IDRef` fields do not need this step because their wire format is already scalar.

The SaaS example's `example-projects/saas/app/views/label.py` shows this in a `create_and_attach` route that creates a sibling row, flushes it to get an id, and then builds a second row with `IDRef` references.

---

## Raise HTTP errors from any method

Every method runs inside a request context, so you can raise `fastapi.HTTPException` (or `fr.exc.Forbidden` / `fr.exc.NotFound`) at any point:

```python
import fastapi

    async def create(self, schema_obj):
        if not self.request.state.user.is_admin:
            raise fastapi.HTTPException(403, "Admin access required")
        return await super().create(schema_obj)
```

For permission gating specifically, prefer `authorize` (above) — it runs at the right phase of the handler and keeps the business verb auth-free.

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
