# Override CRUD Behavior and Add Custom Endpoints

FastAPI-Restly generates five CRUD endpoints per view. Real applications still need custom fields, row visibility, side effects, and non-CRUD actions. This guide shows which method to override.

Every CRUD verb has **three tiers**. Most overrides belong in the lowest tier. For the conceptual model, read [The Handle Design](the_handle_design.md). For the complete method list, see [View Method Surface](api_reference.md#view-method-surface).

Register each concrete view with {func}`fr.include_view(app, ViewClass) <fastapi_restly.views.include_view>` or the decorator shortcut. In larger apps, define view classes in view modules and include them from app/router modules.

---

## The three tiers of a CRUD verb

The conceptual model — both request lifecycles, why the handler owns the
commit, and the full "which method do I override for X?" table — lives in
[How Overrides Work: The Three Tiers](the_handle_design.md). The short
version:

| Tier | Methods | Owns | Override to… |
|---|---|---|---|
| **1. Route shell** (wire) | {meth}`create_endpoint <fastapi_restly.views.RestView.create_endpoint>`, {meth}`get_many_endpoint <fastapi_restly.views.RestView.get_many_endpoint>`, {meth}`get_one_endpoint <fastapi_restly.views.RestView.get_one_endpoint>`, {meth}`update_endpoint <fastapi_restly.views.RestView.update_endpoint>`, {meth}`delete_endpoint <fastapi_restly.views.RestView.delete_endpoint>` | The {func}`@route <fastapi_restly.views.route>`, the FastAPI signature, `response_model`, and {meth}`to_response <fastapi_restly.views.BaseRestView.to_response>` | Change the **HTTP contract** (status code, response shape, headers) |
| **2. Request handler** | {meth}`handle_create <fastapi_restly.views.RestView.handle_create>`, {meth}`handle_get_many <fastapi_restly.views.RestView.handle_get_many>`, {meth}`handle_get_one <fastapi_restly.views.RestView.handle_get_one>`, {meth}`handle_update <fastapi_restly.views.RestView.handle_update>`, {meth}`handle_delete <fastapi_restly.views.RestView.handle_delete>` | {meth}`authorize <fastapi_restly.views.RestView.authorize>` and the commit bracket ({meth}`before_commit <fastapi_restly.views.RestView.before_commit>` → commit → {meth}`after_commit <fastapi_restly.views.RestView.after_commit>`); returns the domain object | Change **orchestration / timing** (custom transaction, async delete) without re-declaring the route |
| **3. Business verb** (domain) | {meth}`create <fastapi_restly.views.RestView.create>`, {meth}`get_many <fastapi_restly.views.RestView.get_many>`, {meth}`get_one <fastapi_restly.views.RestView.get_one>`, {meth}`update <fastapi_restly.views.RestView.update>`, {meth}`delete <fastapi_restly.views.RestView.delete>` | The domain operation: build / apply / save. **Auth-free and commit-free.** | Change **domain logic** (hash a password, derive a slug, compute a field) — the usual override point |

**Default rule:** override the **business verb** for domain logic. Use **`handle_<verb>`** for orchestration or transaction changes. Replace the **route shell** only for HTTP contract changes.

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

{attr}`self.request <fastapi_restly.views.BaseRestView.request>` is the live FastAPI `Request`. {attr}`self.session <fastapi_restly.views.RestView.session>` is the injected async SQLAlchemy session. Both are available in every method.

### `update` — run validation before saving

{meth}`update <fastapi_restly.views.RestView.update>` receives the already-loaded object (fetched and visibility-scoped by {meth}`handle_update <fastapi_restly.views.RestView.handle_update>`), not the id:

```python
    async def update(self, obj, schema_obj):
        if obj.locked:
            raise fastapi.HTTPException(409, "Cannot update a locked record")
        obj = await self.update_object(obj, schema_obj)
        return await self.save_object(obj)
```

(soft-delete-recipe)=

### `delete` — soft-delete instead of removing the row

{meth}`delete <fastapi_restly.views.RestView.delete>` also receives the loaded object. Flip a timestamp instead of deleting:

```python
    async def delete(self, obj):
        obj.deleted_at = datetime.now(timezone.utc)
        await self.session.flush()
        # Do NOT call super() — that would remove the row.
```

For reusable soft-delete that also hides rows on read, see `SoftDeleteMixin` in [Composing views with mixins](howto_compose_views_with_mixins.md).

### `get_one` — eager-load extra relationships

The default {meth}`get_one <fastapi_restly.views.RestView.get_one>` loads through {meth}`build_query <fastapi_restly.views.RestView.build_query>` and schema-derived loader options. If one endpoint needs extra eager loading, keep `build_query` in the query so visibility still applies:

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

For post-query decoration, override {meth}`get_many <fastapi_restly.views.RestView.get_many>` and delegate to `super()`. For filters, joins, or eager loading that apply to every read, prefer {meth}`build_query <fastapi_restly.views.RestView.build_query>`.

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

- **Visibility** — which rows exist at all for this caller — lives in {meth}`build_query <fastapi_restly.views.RestView.build_query>`.
- **Policy** — whether this caller may perform the action — lives in {meth}`authorize <fastapi_restly.views.RestView.authorize>`, called by the handler.

### `build_query` — scope every read at once

{meth}`build_query <fastapi_restly.views.RestView.build_query>` is the read-scope override point. {meth}`get_many <fastapi_restly.views.RestView.get_many>` (list + count) and {meth}`get_one <fastapi_restly.views.RestView.get_one>` both use it, so one filter covers:

- the listed page,
- the pagination total ({meth}`count <fastapi_restly.views.RestView.count>` counts the same scoped query),
- and single-row fetches — a row hidden from the list returns **404** from `GET /{id}` as well, with no extra code.

Because {meth}`handle_update <fastapi_restly.views.RestView.handle_update>` and {meth}`handle_delete <fastapi_restly.views.RestView.handle_delete>` load through `get_one` first, they inherit the same visibility check.

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

{meth}`authorize(action, obj=None, data=None) <fastapi_restly.views.RestView.authorize>` runs inside `handle_<verb>`: before create, and after the object is loaded for {meth}`get_one <fastapi_restly.views.RestView.get_one>` / {meth}`update <fastapi_restly.views.RestView.update>` / {meth}`delete <fastapi_restly.views.RestView.delete>`. Override it to enforce policy:

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

`action` is the verb, `obj` is the loaded row, and `data` is the validated request payload. Authentication itself is yours to wire; Restly calls `authorize` and maps {class}`fr.exc.Forbidden <fastapi_restly.exc.Forbidden>` / {class}`fr.exc.NotFound <fastapi_restly.exc.NotFound>` to HTTP responses.

Visibility belongs in {meth}`build_query <fastapi_restly.views.RestView.build_query>`, not here — raising from `authorize` produces a 403, whereas hiding a row through `build_query` produces a 404.

---

## Tier 2: override `handle_<verb>` for orchestration

Use `handle_<verb>` to change orchestration: transaction handling, side-effect timing, or authorize/load order. The handler owns {meth}`authorize <fastapi_restly.views.RestView.authorize>` and the commit bracket.

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

- {meth}`before_commit(action, new, old=None) <fastapi_restly.views.RestView.before_commit>` — runs inside the transaction, committed atomically with the write. Use it for outbox rows or audit rows.
- {meth}`after_commit(action, new, old=None) <fastapi_restly.views.RestView.after_commit>` — runs after the write is durable. Use it for email, webhooks, or cache invalidation.

`old` is a snapshot dict of the object's column values before the mutation (see {meth}`snapshot <fastapi_restly.views.BaseRestView.snapshot>`), which enables dirty detection:

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

When the derivation should fire on every insert regardless of which view
created the row (audit stamps, slug derivation, denormalised counters), prefer
a SQLAlchemy `before_insert` mapper event listener instead:

```python
from sqlalchemy import event

@event.listens_for(Article, "before_insert")
def _set_slug(mapper, connection, target):
    target.slug = slugify(target.title)
```

See SQLAlchemy's [mapper events
documentation](https://docs.sqlalchemy.org/en/20/orm/events.html#mapper-events)
for the full event API.

---

(domain-utilities)=

## Domain utilities — call, don't override

The business verbs are built from a handful of low-level utilities. **Call** them from your {meth}`create <fastapi_restly.views.RestView.create>` / {meth}`update <fastapi_restly.views.RestView.update>` / {meth}`delete <fastapi_restly.views.RestView.delete>`; they are not the override point.

| Method | What it does |
|---|---|
| `self.make_new_object(schema_obj)` | Construct a new ORM object from the schema and add it to the session (the cooperative override point for create-time field stamping). **Does not flush.** |
| `self.update_object(obj, schema_obj)` | Apply writable fields onto an existing object (the cooperative override point for update-time field stamping). **Does not flush.** |
| `self.save_object(obj)` | Flush and refresh `obj` from the database. **Does not commit.** |
| `self.delete_object(obj)` | Remove `obj` and flush. **Does not commit.** |

The same operations are available as free functions for use outside a view — scripts, workers, services: {func}`fr.objects.async_make_new_object <fastapi_restly.objects.async_make_new_object>`, {func}`async_update_object <fastapi_restly.objects.async_update_object>`, {func}`async_save_object <fastapi_restly.objects.async_save_object>`, {func}`async_delete_object <fastapi_restly.objects.async_delete_object>` (and their sync counterparts). See [Advanced Object Helpers](api_reference.md#advanced-object-helpers).

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

Generated route shells return through {meth}`self.to_response(obj_or_list, shape) <fastapi_restly.views.BaseRestView.to_response>`, where `shape` is {attr}`SINGLE <fastapi_restly.views.ResponseShape.SINGLE>`, {attr}`LISTING <fastapi_restly.views.ResponseShape.LISTING>`, or {attr}`EMPTY <fastapi_restly.views.ResponseShape.EMPTY>`. Override it for envelopes or shape-wide response behavior:

```python
    def to_response(self, obj_or_list, shape=fr.ResponseShape.SINGLE):
        if shape is fr.ResponseShape.SINGLE:
            return {"data": self.to_response_schema(obj_or_list)}
        return super().to_response(obj_or_list, shape)
```

If this changes a generated route's HTTP contract, also replace that route shell
and set a matching `response_model`; otherwise FastAPI response validation and
OpenAPI still use the generated schema. See
[Response Envelopes and List Metadata](howto_response_schema.md) for the full pattern.

`to_response` is keyed on wire shape, not action. It cannot distinguish {meth}`create <fastapi_restly.views.RestView.create>` from {meth}`get_one <fastapi_restly.views.RestView.get_one>`; both are `SINGLE`. For one verb's HTTP contract, override that route shell:

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

For object serialization, {meth}`to_response_schema(obj) <fastapi_restly.views.BaseRestView.to_response_schema>` builds the configured schema, strips `WriteOnly` fields, normalizes relationship ids, and validates through Pydantic. Override it for a different projection or a faster trusted path:

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

Replace {meth}`get_many_endpoint <fastapi_restly.views.RestView.get_many_endpoint>` when the list response contract changes, for example custom headers. Keep the `query_params` parameter if you want Restly's generated filter, sort, and pagination query parameters:

```python
import fastapi
import json

@fr.include_view(app)
class ProductView(fr.AsyncRestView):
    prefix = "/products"
    model = Product
    schema = ProductRead

    @fr.get("/")
    async def get_many_endpoint(self, query_params):
        result = await self.handle_get_many(query_params)
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

React Admin views use this same pattern: they replace {meth}`get_many_endpoint <fastapi_restly.views.RestView.get_many_endpoint>` for the `ra-data-simple-rest` wire contract and keep the standard verbs and handlers.

---

## Add a custom read route

Use {func}`@fr.get <fastapi_restly.views.get>` for computed read endpoints. Call {meth}`get_one(id) <fastapi_restly.views.RestView.get_one>` for scoped load + 404, or {meth}`handle_get_one(id) <fastapi_restly.views.RestView.handle_get_one>` to include read authorization:

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

Use {func}`@fr.post <fastapi_restly.views.post>` (or {func}`@fr.patch <fastapi_restly.views.patch>`, {func}`@fr.delete <fastapi_restly.views.delete>`) for state-change actions such as archive, publish, or recalculate. Use one of two shapes:

**Bracket the mutation with {meth}`write_action <fastapi_restly.views.RestView.write_action>`.** Load with {meth}`handle_get_one(id) <fastapi_restly.views.RestView.handle_get_one>`, then use `self.write_action` under a custom action name:

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

**Run a full create/update through a handler.** If an action is create or update under another URL, build the input schema and call {meth}`handle_create <fastapi_restly.views.RestView.handle_create>` / {meth}`handle_update <fastapi_restly.views.RestView.handle_update>`:

```python
    @fr.post("/{id}/duplicate", status_code=201)
    async def duplicate(self, id: int):
        original = await self.get_one(id)
        payload = self.schema_create(name=f"{original.name} (copy)", ...)
        new_order = await self.handle_create(payload)
        return self.to_response_schema(new_order)
```

Reusing `handle_<verb>` inherits authorization and the commit bracket.

For a **create-shaped** action that should run under its own `write_action`
bracket instead, deposit the new object on the yielded handle:

```python
    async with self.write_action("create", data=req) as w:
        w.obj = await self.make_new_object(req)
    return self.to_response(w.obj)
```

### Relationship references in custom routes

When a custom route constructs schemas itself (`model_construct()` skips
validation), {class}`IDRef <fastapi_restly.schemas.IDRef>` fields need explicit wrapping — the recipe lives in
[Work with Foreign Keys and Relationships](#idref-custom-routes).

---

## Raise HTTP errors from any method

Every method runs inside a request context, so you can raise `fastapi.HTTPException` (or {class}`fr.exc.Forbidden <fastapi_restly.exc.Forbidden>` / {class}`fr.exc.NotFound <fastapi_restly.exc.NotFound>`) at any point:

```python
import fastapi

    async def create(self, schema_obj):
        if not self.request.state.user.is_admin:
            raise fastapi.HTTPException(403, "Admin access required")
        return await super().create(schema_obj)
```

For permission gating specifically, prefer {meth}`authorize <fastapi_restly.views.RestView.authorize>` (above) — it runs at the right phase of the handler and keeps the business verb auth-free.

---

## Exclude generated routes

Set {attr}`exclude_routes <fastapi_restly.views.BaseRestView.exclude_routes>` to suppress specific generated endpoints:

```python
@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    exclude_routes = [fr.ViewRoute.DELETE, fr.ViewRoute.UPDATE]
```

Valid values are: {attr}`fr.ViewRoute.GET_MANY <fastapi_restly.views.ViewRoute.GET_MANY>`, {attr}`fr.ViewRoute.GET_ONE <fastapi_restly.views.ViewRoute.GET_ONE>`, {attr}`fr.ViewRoute.CREATE <fastapi_restly.views.ViewRoute.CREATE>`, {attr}`fr.ViewRoute.UPDATE <fastapi_restly.views.ViewRoute.UPDATE>`, {attr}`fr.ViewRoute.DELETE <fastapi_restly.views.ViewRoute.DELETE>`. Route-shell-name strings such as `"delete_endpoint"` are also accepted; any other string raises `AttributeError` at startup.

---

## Choosing between `@fr.route` and the shorthand decorators

Prefer {func}`@fr.get <fastapi_restly.views.get>`, {func}`@fr.post <fastapi_restly.views.post>`, {func}`@fr.put <fastapi_restly.views.put>`, {func}`@fr.patch <fastapi_restly.views.patch>`, and {func}`@fr.delete <fastapi_restly.views.delete>` for most endpoints. They set the HTTP method automatically and apply Restly's default status codes: `@fr.get`/`@fr.put`/`@fr.patch` use 200, `@fr.post` uses 201, and `@fr.delete` uses 204.

Use {func}`@fr.route(path, methods=[...], ...) <fastapi_restly.views.route>` only when you need full manual control over route options — for example, to register a single path under multiple HTTP methods, or to set a non-standard response code:

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
| {attr}`self.session <fastapi_restly.views.RestView.session>` | `AsyncSession` | The current database session |
| {attr}`self.request <fastapi_restly.views.BaseRestView.request>` | `fastapi.Request` | The live HTTP request |
| {attr}`self.model <fastapi_restly.views.BaseRestView.model>` | `type[DeclarativeBase]` | The SQLAlchemy model class |
| {attr}`self.schema <fastapi_restly.views.BaseRestView.schema>` | `type[pydantic.BaseModel]` | The Pydantic response schema |

Any class-level `Annotated` dependency you declare on the view (e.g. a current user) is also injected and available as an instance attribute.

## See also

- [The Handle Design](the_handle_design.md) — the full three-tier model and the commit bracket.
- [Composing views with mixins](howto_compose_views_with_mixins.md) — structural stamping and scoping through cooperative mixins.
- [View Method Surface](api_reference.md#view-method-surface) — the complete classified method list.
