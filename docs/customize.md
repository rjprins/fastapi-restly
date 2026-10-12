# Request Lifecycle & Hooks

{class}`RestView <fastapi_restly.views.RestView>` and {class}`AsyncRestView <fastapi_restly.views.AsyncRestView>` define complete CRUD
endpoints. Because views are class-based, a subclass changes an endpoint's
behavior by overriding the method that controls it: stamp a field server-side,
archive instead of delete, or hide soft-deleted rows. This page first explains
how each view handles a request, then works through the override points and
recipes that follow from that structure.

[CRUD Routes](rest_views.md) covers the default CRUD contract
and its class configuration. This page starts where those defaults stop. A
plain {class}`View <fastapi_restly.views.View>` defines no CRUD endpoints.
[Views](class_based_views.md) covers the class mechanics shared by
all views.

:::{note}
To *add* routes rather than change existing ones, declare a method with
{func}`@fr.get <fastapi_restly.views.get>` or {func}`@fr.post <fastapi_restly.views.post>`,
{ref}`as on any view <what-is-a-class-based-view>`. Recipes
are in [Add a custom read route](#add-a-custom-read-route) and
[Add a custom action route](#add-a-custom-action-route) below.
:::

(view-request-steps)=
## How a view handles a request

Take `POST /`, the create route, and follow the request inward. FastAPI calls
{meth}`create_endpoint <fastapi_restly.views.RestView.create_endpoint>`, the
endpoint method. It calls
{meth}`handle_create <fastapi_restly.views.RestView.handle_create>`, the
handler. The handler handles the steps of the request, in this order:

```
POST /
  └─ create_endpoint(schema_obj)                 # endpoint method: the HTTP contract
       ├─ handle_create(schema_obj)              # handler: handles the steps (final)
       │    ├─ authorize("create", data=schema_obj)
       │    ├─ create(schema_obj)                 # business method: the domain change
       │    │    ├─ make_new_object(schema_obj)   # build the ORM object (final)
       │    │    └─ save_object(obj)              # flush and refresh, no commit (final)
       │    ├─ before_action_commit("create", new=obj)
       │    ├─ commit
       │    └─ after_action_commit("create", new=obj)
       └─ to_response(obj)
```

To change what a route does, you replace a step, such as `create` or
`authorize`. The handler itself is final, so every CRUD route runs all of its
steps.

The **endpoint method**, `<verb>_endpoint`, is the method FastAPI routes to.
It owns the HTTP contract: the {func}`@route <fastapi_restly.views.route>`
decorator, the FastAPI signature, `response_model`, and the final
{meth}`to_response <fastapi_restly.views.BaseRestView.to_response>` call.
Override it to change the contract, such as the status code. See
[Replace an endpoint method](#replace-an-endpoint-method-to-change-the-http-contract).

The **handler**, `handle_<verb>`, handles the steps between. It runs
{meth}`authorize <fastapi_restly.views.RestView.authorize>` and calls the
business method. On a write, it ends with the **commit bracket**:
{meth}`before_action_commit <fastapi_restly.views.RestView.before_action_commit>`,
the commit, then
{meth}`after_action_commit <fastapi_restly.views.RestView.after_action_commit>`.
It returns the domain object, and only the delete handler returns nothing.
A view class that defines a handler fails at class definition. A custom route
calls the handler to run the same steps. To move the commit to an outer block,
see [Commit several writes together](#shared-write-action-commit).

The **business method** is the bare verb:
{meth}`create <fastapi_restly.views.RestView.create>`,
{meth}`update <fastapi_restly.views.RestView.update>`,
{meth}`delete <fastapi_restly.views.RestView.delete>`,
{meth}`get_one <fastapi_restly.views.RestView.get_one>`, or
{meth}`get_many <fastapi_restly.views.RestView.get_many>`. It makes the domain
change: build, apply, save. It does not check authorization and does not
commit. So when your override runs, `authorize` has already passed, and the
commit comes after it.

The names are the same for all five verbs: `update_endpoint` calls
`handle_update`, which calls `update`, and so on.

{meth}`update <fastapi_restly.views.RestView.update>` and
{meth}`delete <fastapi_restly.views.RestView.delete>` have the same steps,
with a load first. Their handlers load the row through
{meth}`get_one <fastapi_restly.views.RestView.get_one>`, so a hidden row is a
404. Then they authorize against the loaded row and take a
{meth}`snapshot(obj) <fastapi_restly.views.BaseRestView.snapshot>` as `old`.
The business method runs next, then the same commit bracket. The hooks get
both `new` and `old`, so they can see what changed.
[Transaction hooks](#transaction-hooks) has recipes for them.

(read-request-steps)=
### A read: `get_one`

Reads have no commit bracket. Read access has two separate concerns,
**visibility** and **policy**, and a different step handles each:

```
GET /{id}
  └─ get_one_endpoint(id)            # endpoint method
       ├─ handle_get_one(id)         # handler
       │    ├─ get_one(id)           # business method
       │    │    └─ the view scope   # VISIBILITY: soft-delete, role, row-level
       │    │                        #   a hidden row is a clean 404 for every caller
       │    └─ authorize("get_one", obj=obj)   # POLICY: read-auth on the loaded row
       └─ to_response(obj)
```

Because {meth}`get_one <fastapi_restly.views.RestView.get_one>` applies the view scope ([Scopes](scopes.md)), replaceable visibility lives in one
place across list, count, and single-row reads: a hidden row returns 404 from
`GET /{id}`. `get_one` itself stays auth-free; {meth}`authorize <fastapi_restly.views.RestView.authorize>` handles policy.

{meth}`get_many <fastapi_restly.views.RestView.get_many>` works the same way: the scope establishes visibility,
{meth}`apply_list_params <fastapi_restly.views.RestView.apply_list_params>` applies filtering, sorting, and pagination, and
{meth}`count <fastapi_restly.views.RestView.count>` produces the total, with `authorize("get_many")` added by
{meth}`handle_get_many <fastapi_restly.views.RestView.handle_get_many>`.

## Which method do I override for X?

The table below maps the change you want to make to the method that owns it:

| I want to change…                       | Use                               |
|-----------------------------------------|-----------------------------------|
| Domain logic (hash, derive, compute)    | {meth}`create <fastapi_restly.views.RestView.create>` / {meth}`update <fastapi_restly.views.RestView.update>` / {meth}`delete <fastapi_restly.views.RestView.delete>` |
| One commit over several writes          | {meth}`shared_write_action_commit <fastapi_restly.views.RestView.shared_write_action_commit>` in a custom route |
| A lookup by another key (slug, email)   | {meth}`handle_get_one <fastapi_restly.views.RestView.handle_get_one>` with a predicate, in a custom route ([below](#natural-key-route)) |
| A list of a subset (one project's tasks) | {meth}`handle_get_many <fastapi_restly.views.RestView.handle_get_many>` with `where=`, in a custom route ([Scopes](#per-read-where)) |
| The HTTP contract (status, signature)   | `<verb>_endpoint`                 |
| Read scope / row visibility             | {attr}`scope <fastapi_restly.views.BaseRestView.scope>` ([Scopes](scopes.md)) |
| Filter / sort / pagination grammar      | {meth}`apply_list_params <fastapi_restly.views.RestView.apply_list_params>` |
| The list total                          | {meth}`count <fastapi_restly.views.RestView.count>` |
| Authorization / policy                  | {meth}`authorize <fastapi_restly.views.RestView.authorize>` (override to gate) |
| Server-stamped fields (audit/tenant)    | a column default on the model ([below](#server-stamped-fields-column-defaults-on-the-model)) |
| A side effect inside the commit (outbox/audit) | {meth}`before_action_commit <fastapi_restly.views.RestView.before_action_commit>` |
| A side effect after the commit (email/webhook) | {meth}`after_action_commit <fastapi_restly.views.RestView.after_action_commit>` |
| The response shape                      | {meth}`to_response <fastapi_restly.views.BaseRestView.to_response>` |

Start at the business method. Move a side effect that depends on the commit
to a transaction hook, and touch the endpoint method only when the HTTP
contract itself changes.

The sections below give recipes for each of these override points.

## Override the business methods

Each business method maps to one domain operation, so override only the one
you need. These methods are auth-free and commit-free; the handler adds
authorization and commit handling around them.

(password-hash-example)=
### `create`: hash a password

Hashing a password is domain logic, so it belongs in
{meth}`create <fastapi_restly.views.RestView.create>`. The commit bracket
commits after this method returns:

```python
import fastapi_restly as fr
from sqlalchemy.orm import Mapped

from .auth import hash_password
from .models import Base


class User(fr.IDMixin, Base):
    email: Mapped[str]
    password: Mapped[str]  # stores the hash. UserView.create writes it


class UserSchema(fr.IDSchema):
    email: str
    password: fr.WriteOnly[str]  # accepted on input, never serialized


@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserSchema

    async def create(self, schema_obj):
        obj = await self.make_new_object(schema_obj)
        obj.password = hash_password(schema_obj.password)
        return await self.save_object(obj)
```

{meth}`handle_create <fastapi_restly.views.RestView.handle_create>` still authorizes and runs the commit bracket. The override only
changes the domain step. The schema field and the column share a name because
{meth}`make_new_object <fastapi_restly.views.RestView.make_new_object>` passes every writable schema field to the model constructor:
the plaintext lands in `password` first, and the override replaces it with
the hash before the flush. `fr.WriteOnly` keeps the field out of every
response.

To set a field that the server owns, such as the author, use a
[column default](#server-stamped-fields-column-defaults-on-the-model) instead
of `create`. It also covers rows that other code creates.

### `update`: run validation before saving

{meth}`update <fastapi_restly.views.RestView.update>` receives the already-loaded object (fetched and visibility-scoped by {meth}`handle_update <fastapi_restly.views.RestView.handle_update>`), not the id:

```python
    async def update(self, obj, schema_obj):
        if obj.locked:
            raise fastapi.HTTPException(409, "Cannot update a locked record")
        obj = await self.update_object(obj, schema_obj)
        return await self.save_object(obj)
```

(soft-delete-recipe)=

### `delete`: soft-delete instead of removing the row

{meth}`delete <fastapi_restly.views.RestView.delete>` also receives the loaded object. Flip a timestamp instead of deleting:

```python
    async def delete(self, obj):
        obj.deleted_at = datetime.now(timezone.utc)
        await self.session.flush()
        # Do not call super(); that would remove the row.
```

For reusable soft-delete that also hides rows on read, see `SoftDeleteMixin` in [Compose Views with Mixins](howto_compose_views_with_mixins.md).

### `get_many`: decorate results after the query

For post-query decoration, override {meth}`get_many <fastapi_restly.views.RestView.get_many>` and delegate to `super()`.
Use a [scope](scopes.md) for row visibility.
Put joins and ordering for the list in
{meth}`apply_list_params <fastapi_restly.views.RestView.apply_list_params>`.
Put eager loading in
{meth}`get_relationship_loader_options <fastapi_restly.views.BaseRestView.get_relationship_loader_options>`.

```python
    async def get_many(self, list_params, *, scope=None):
        result = await super().get_many(list_params, scope=scope)
        for obj in result.objects:
            obj._display_name = derive_display_name(obj)
        return result
```

## Read scope: the view scope + `authorize`

[A read](#read-request-steps) above split read
access into two independent concerns; each has its own override point:

- Visibility, meaning which rows exist at all for this caller, lives in the
  view scope: a [query clause](clauses.md) declared as the model's
  `default_scope` or the view's
  {attr}`scope <fastapi_restly.views.BaseRestView.scope>`.
  [Scopes](scopes.md) owns that topic.
- Policy, meaning whether this caller may perform the action, lives in {meth}`authorize <fastapi_restly.views.RestView.authorize>`, which the handler calls.

(read-scope)=
### The scope: filter every read at once

The following view scopes every read to rows owned by the requesting user:

```python
class Current(fr.ContextNamespace):
    user_id: fr.ContextParam[int]

@fr.include_view(app)
class DocumentView(fr.AsyncRestView):
    prefix = "/documents"
    model = Document
    schema = DocumentSchema
    scope = fr.where_clause(Document.owner_id == Current.user_id)
```

with ``Current.depends(...)`` binding ``user_id`` per request.
{meth}`get_many <fastapi_restly.views.RestView.get_many>` (list and count) and
{meth}`get_one <fastapi_restly.views.RestView.get_one>` both apply the scope,
so one declaration covers:

- the listed page,
- the pagination total ({meth}`count <fastapi_restly.views.RestView.count>` counts the same scoped query),
- and single-row fetches: a row hidden from the list returns 404 from `GET /{id}` as well, with no extra code.

Because {meth}`handle_update <fastapi_restly.views.RestView.handle_update>` and {meth}`handle_delete <fastapi_restly.views.RestView.handle_delete>` load through `get_one` first, they inherit the same visibility check. `get_one` stays auth-free even though it 404s on hidden rows: visibility comes from the query, and custom routes that call `get_one(id)` get the same scope.

[Scopes](scopes.md) covers composing clauses, the model-wide
`default_scope`, and per-read scope replacement. Overriding
``build_query()`` is removed. A view that still defines it fails at class
definition with a pointer to
[Migrating from build_query](#migrating-build-query).

(authorize-gate)=
### `authorize`: gate the action

{meth}`authorize(action, obj=None, data=None) <fastapi_restly.views.RestView.authorize>` runs inside `handle_<verb>`: before {meth}`create <fastapi_restly.views.RestView.create>` and {meth}`get_many <fastapi_restly.views.RestView.get_many>`, and after the object is loaded for {meth}`get_one <fastapi_restly.views.RestView.get_one>` / {meth}`update <fastapi_restly.views.RestView.update>` / {meth}`delete <fastapi_restly.views.RestView.delete>`. Override it to enforce policy:

```python
from typing import Annotated
from fastapi import Depends

@fr.include_view(app)
class InvoiceView(fr.AsyncRestView):
    prefix = "/invoices"
    model = Invoice
    schema = InvoiceSchema
    current_user: Annotated[User, Depends(get_current_user)]

    async def authorize(self, action, obj=None, data=None):
        if action in ("create", "update", "delete") and not self.current_user.is_staff:
            raise fr.exc.Forbidden()
        if action == "update" and obj.posted:
            raise fr.exc.Forbidden("Posted invoices are immutable")
```

`action` is the verb, `obj` is the loaded row, and `data` is the validated request payload. You add authentication yourself; Restly calls `authorize` and maps {class}`fr.exc.Forbidden <fastapi_restly.exc.Forbidden>` / {class}`fr.exc.NotFound <fastapi_restly.exc.NotFound>` to HTTP responses.

`current_user` is a class-level dependency, injected per request like any other ([Dependency injection on class attributes](class_based_views.md#dependency-injection-on-class-attributes)). {attr}`self.request <fastapi_restly.views.BaseRestView.request>` is the live FastAPI `Request`. {attr}`self.session <fastapi_restly.views.RestView.session>` is the injected async SQLAlchemy session. Both are available in every method.

Visibility belongs in the [scope](scopes.md), not here: raising from `authorize` produces a 403, whereas a row outside the scope produces a 404.

(transaction-hooks)=
## Transaction hooks: `before_action_commit` / `after_action_commit`

The write handlers call two hooks around the commit:

- {meth}`before_action_commit(action, new, old=None) <fastapi_restly.views.RestView.before_action_commit>` runs inside the transaction and is committed atomically with the write. Use it for outbox rows or audit rows.
- {meth}`after_action_commit(action, new, old=None) <fastapi_restly.views.RestView.after_action_commit>` runs after the write is durable. Use it for email, webhooks, or cache invalidation.

`old` is a snapshot dict of the object's column values before the mutation (see {meth}`snapshot <fastapi_restly.views.BaseRestView.snapshot>`), which enables dirty detection:

```python
    async def after_action_commit(self, action, new, old=None):
        if action == "update" and old["status"] != new.status:
            await notify_status_change(new.id, new.status)
```

On a delete, `new` is `None` and `old` holds the removed row's columns. A
delete that finishes off-request marks the row in the
[`delete` business method](#soft-delete-recipe), then enqueues the real delete
once the mark is durable:

```python
    async def delete(self, obj):
        obj.status = "pending_deletion"
        await self.session.flush()  # no super(): the row stays

    async def after_action_commit(self, action, new, old=None):
        if action == "delete":
            await enqueue_async_delete(old["id"])  # the real delete runs off-request
```

(after-action-commit-raises)=
### When `after_action_commit` raises

The write is already committed, so an exception from the hook cannot undo it.
The request still fails, and a client that retries repeats the write. For a
best-effort effect, catch and log the error in the hook:

```python
    async def after_action_commit(self, action, new, old=None):
        if action == "create":
            try:
                await send_confirmation_email(new.id)
            except Exception:  # the order is saved; don't fail the request
                logger.exception("Confirmation email failed for order %s", new.id)
```

An effect that must happen belongs in an outbox row written in
`before_action_commit`, which a worker delivers and retries. Make delivery
idempotent, since a retry can repeat it. FastAPI's `BackgroundTasks` is no
substitute: a crash loses the task, and nothing retries it.

## Server-stamped fields: column defaults on the model

A field the server owns (an audit id, a tenant id) is a column default that reads a bound {class}`fr.ContextNamespace <fastapi_restly.clauses.ContextNamespace>` member. It fires on every write path, whichever view or helper built the row, so nothing on the view has to run:

```python
class Article(fr.TimestampsMixin, fr.IDMixin, Base):
    title: Mapped[str]
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id"), init=False, insert_default=Current.user_id
    )
```

`init=False` leaves the field out of the constructor. SQLAlchemy calls
`Current.user_id` when it inserts the row. Pass the member without
parentheses, as [Read the current user in a column default](#current-column-defaults)
explains.

Keep the field `fr.ReadOnly` on the schema and out of any [explicit write schema](#explicit-write-schemas). With `init=False`, a payload value for it would fail in the constructor. Stamping from the view instead, in `create`, only covers that verb: a `create` override does not call `super()`, and the free `fr.objects` helpers never see the view. See [Compose Views with Mixins](howto_compose_views_with_mixins.md) for the tenant, audit, and soft-delete pieces the SaaS example ships.

A derivation that needs more than a value, a slug from the title or a denormalised counter, is a SQLAlchemy `before_insert` mapper event on the same model:

```python
from sqlalchemy import event

@event.listens_for(Article, "before_insert")
def _set_slug(mapper, connection, target):
    target.slug = slugify(target.title)
```

See SQLAlchemy's [mapper events documentation](https://docs.sqlalchemy.org/en/20/orm/events.html#mapper-events) for the full event API.

(domain-utilities)=

## Domain utilities: call, don't override

The business methods are built from three utilities. Call them from your {meth}`create <fastapi_restly.views.RestView.create>` / {meth}`update <fastapi_restly.views.RestView.update>` overrides; they are final, and a view class that defines one, itself or through a mixin, fails at class definition. A server-stamped field is a [column default on the model](#server-stamped-fields-column-defaults-on-the-model); a per-write side effect is a [transaction hook](#transaction-hooks-before_action_commit--after_action_commit).

| Method | What it does |
|---|---|
| `self.make_new_object(schema_obj)` | Constructs a new ORM object from the schema, resolving references and skipping the fields the schema marks read-only, and adds it to the session. Does not flush. |
| `self.update_object(obj, schema_obj)` | Applies writable fields onto an existing object, resolving references. Does not flush. |
| `self.save_object(obj)` | Flushes and refreshes `obj`, then eager-loads the relationships the response class names. Does not commit. |

The same operations are available as free functions for use outside a view (scripts, workers, services): {func}`fr.objects.async_make_new_object <fastapi_restly.objects.async_make_new_object>`, {func}`async_update_object <fastapi_restly.objects.async_update_object>`, {func}`async_save_object <fastapi_restly.objects.async_save_object>`, {func}`async_delete_object <fastapi_restly.objects.async_delete_object>`, plus their sync counterparts. See [Advanced Object Helpers](#advanced-object-helpers).

An import script, for example, can build and persist an object with the same semantics a view would use:

```python
from fastapi_restly.objects import async_make_new_object, async_save_object


async def import_user(session, payload) -> User:
    user = await async_make_new_object(session, User, payload)
    user.password = hash_password(payload.password)
    await async_save_object(session, user)
    await session.commit()
    return user
```

Because none of these commit, the same code works inside a view or worker; only the caller owns the transaction.

## Replace an endpoint method to change the HTTP contract

Business methods and hooks change behavior while preserving the default CRUD route's HTTP contract. Replace the endpoint method when the HTTP contract itself must change: response shape, headers, status code, or query-parameter semantics.

To replace a route, define the same endpoint-method name and add a route decorator. Usually, delegate to the handler and only reshape the response:

```python
@fr.include_view(app)
class ProductView(fr.AsyncRestView):
    prefix = "/products"
    model = Product
    schema = ProductSchema

    @fr.delete("/{id}", status_code=200)
    async def delete_endpoint(self, id: int):
        obj = await self.get_one(id)               # load (scoped, 404)
        serialized = self.to_single_response(obj).model_dump(mode="json")
        await self.handle_delete(id)               # authorize + delete + commit
        return serialized
```

At view registration, a directly defined endpoint method replaces the inherited method with the same name. The other inherited endpoint methods remain unchanged.

Registration fills in the view's types for annotations that are missing or `Any`. `id` gets {attr}`id_type <fastapi_restly.views.BaseRestView.id_type>`, `schema_obj` gets {attr}`schema_create <fastapi_restly.views.BaseRestView.schema_create>` or {attr}`schema_update <fastapi_restly.views.BaseRestView.schema_update>`, and the return annotation gets the route's default response model. An annotation you write is kept, so `schema_obj: ProductReplace` validates the request body against `ProductReplace`. `list_params` is the exception: it always gets the view's {attr}`schema_list_params <fastapi_restly.views.BaseRestView.schema_list_params>`.

The default `DELETE /{id}` returns `204 No Content`; this version returns the deleted record, as `ra-data-simple-rest` expects (see [React Admin](howto_react_admin.md)).

### Replace the list endpoint method

Replace {meth}`get_many_endpoint <fastapi_restly.views.RestView.get_many_endpoint>` when the list response contract changes, for example custom headers. Keep the `list_params` parameter if you want Restly's generated [filter, sort, and pagination query parameters](howto_query_modifiers.md):

```python
import fastapi
import json

@fr.include_view(app)
class ProductView(fr.AsyncRestView):
    prefix = "/products"
    model = Product
    schema = ProductSchema

    @fr.get("/")
    async def get_many_endpoint(self, list_params):
        result = await self.handle_get_many(list_params)
        serialized = [
            self.to_single_response(obj).model_dump(mode="json")
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
        serialized = self.to_single_response(obj).model_dump(mode="json")
        await self.handle_delete(id)
        return serialized


@fr.include_view(app)
class ProductView(DeleteReturnsObjectMixin, fr.AsyncRestView):
    prefix = "/products"
    model = Product
    schema = ProductSchema
```

[React Admin views](howto_react_admin.md#share-the-react-admin-contract-across-multiple-views) use this same pattern: they replace {meth}`get_many_endpoint <fastapi_restly.views.RestView.get_many_endpoint>` for the `ra-data-simple-rest` HTTP contract and keep the standard verbs and handlers.

## Add a custom read route

A CRUD view can add routes alongside its inherited CRUD routes. Use {func}`@fr.get <fastapi_restly.views.get>` for computed read endpoints, calling {meth}`get_one(id) <fastapi_restly.views.RestView.get_one>` for a scoped load that 404s on missing rows, or {meth}`handle_get_one(id) <fastapi_restly.views.RestView.handle_get_one>` to include read authorization:

```python
@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserSchema

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

The SaaS example's `GET /users/me` is a read that is not a resource path: it
loads the caller's own row ([`app/users/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/users/views.py)).

(natural-key-route)=
### Look a row up by another key

`id` is the primary key by default, and a SQLAlchemy boolean expression replaces it. Everything else about the load is unchanged: the view's [scope](scopes.md), the schema's loader options, and the same 404.

```python
    @fr.get("/by-email/{email}")
    async def get_by_email(self, email: str) -> UserSchema:
        user = await self.handle_get_one(User.email == email)
        return self.to_response(user)
```

Combine columns with `sqlalchemy.and_(...)`. That is also how a model with a composite primary key is addressed, since an id cannot name one of its rows; passing one raises `NotImplementedError`.

The criterion narrows inside the scope and never past it, so a row the view hides stays a 404 under any key, and {attr}`scope <fastapi_restly.views.BaseRestView.scope>` remains the only way to change what the view sees. More than one match raises SQLAlchemy's `MultipleResultsFound` instead of serving the first row: an ambiguous key is a bug in the criterion.

{meth}`handle_update <fastapi_restly.views.RestView.handle_update>` and {meth}`handle_delete <fastapi_restly.views.RestView.handle_delete>` take the same identity, so `PATCH /users/by-email/{email}` runs the full commit bracket against the row that criterion picks.

## Add a custom action route

Use {func}`@fr.post <fastapi_restly.views.post>` (or {func}`@fr.patch <fastapi_restly.views.patch>`, {func}`@fr.delete <fastapi_restly.views.delete>`) for state-change actions such as archive, publish, or recalculate. Two shapes cover most actions.

The first shape brackets the mutation with {meth}`write_action <fastapi_restly.views.RestView.write_action>`. Load the object with {meth}`get_one(id) <fastapi_restly.views.RestView.get_one>`, then run the mutation inside `self.write_action` under a custom action name; the bracket authorizes that action itself, the same way `handle_update` / `handle_delete` gate only their own action:

```python
@fr.include_view(app)
class OrderView(fr.AsyncRestView):
    prefix = "/orders"
    model = Order
    schema = OrderSchema

    @fr.post("/{id}/archive", status_code=202)
    async def archive(self, id: int):
        order = await self.get_one(id)
        if order.archived:
            raise fastapi.HTTPException(409, "Already archived")
        async with self.write_action("archive", obj=order):
            order.archived = True
        return {"id": order.id, "archived": order.archived}
```

`__aenter__` runs authorization and the snapshot; `__aexit__` runs the commit
bracket, and a raised exception skips the commit. The action name (`"archive"`)
drives authorization and the hooks.

The second shape runs a full create or update through a handler. If an action is a create or update under another URL, build the input schema and call {meth}`handle_create <fastapi_restly.views.RestView.handle_create>` / {meth}`handle_update <fastapi_restly.views.RestView.handle_update>`:

```python
    @fr.post("/{id}/duplicate", status_code=201)
    async def duplicate(self, id: int):
        original = await self.get_one(id)
        payload = self.schema_create(name=f"{original.name} (copy)")
        new_order = await self.handle_create(payload)
        return self.to_single_response(new_order)
```

Reusing `handle_<verb>` inherits authorization and the commit bracket.

For a create-shaped action that should run under its own `write_action`
bracket instead, deposit the new object on the yielded handle:

```python
    async with self.write_action("create", data=schema_obj) as w:
        w.obj = await self.make_new_object(schema_obj)
    return self.to_response(w.obj)
```

The SaaS example runs these shapes. The task state transitions
`POST /tasks/{id}/start`, `complete` and `reopen` use `write_action`
([`app/tasks/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/tasks/views.py)).
`POST /uploads` is a file-upload flow with its own create bracket
([`app/uploads/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/uploads/views.py)).
`POST /task-labels/create-and-attach` commits two rows through one
`write_action` block ([`app/labels/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/labels/views.py)).
`POST /users/{id}/change-password` is an account action
([`app/users/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/users/views.py)).

Internally, `write_action` and the CRUD handlers share {func}`run_write_action <fastapi_restly.views.run_write_action>`.

(shared-write-action-commit)=
### Commit several writes together

The outermost
{meth}`shared_write_action_commit() <fastapi_restly.views.AsyncRestView.shared_write_action_commit>`
block commits once, then the queued `after_action_commit` hooks run. Use it
when several handlers or custom actions should share that commit:

```python
    @fr.post("/bulk", status_code=201)
    async def bulk_create(self, items: list[OrderCreate]):
        async with self.shared_write_action_commit():
            orders = [await self.handle_create(item) for item in items]
        return [self.to_response(order) for order in orders]
```

The block can also combine a handler with related model writes. This is useful
when one logical operation spans more than one model:

```python
    async with self.shared_write_action_commit():
        project = await self.handle_create(project_data)
        self.session.add_all(
            Task(project_id=project.id, title=task.title)
            for task in source_tasks
        )
    return self.to_response(project)
```

The new project, its before-hook writes, and the copied tasks commit together.
If any part fails, none of them commit.

Each action still authorizes, snapshots, mutates, and runs
`before_action_commit`. It then flushes and returns while its changes remain
uncommitted. Its `after_action_commit` has not run yet. Build responses and run
code that depends on an after-hook after the outer block, as in the example.
On `fr.RestView`, use `with` and synchronous handlers under the same method
name.

Nested blocks share the commit when their views share a session. An exception
escaping a deferred-commit block discards its shared queue and prevents that
commit, even if an enclosing deferred-commit block catches the exception. The
session owner still handles rollback. A direct `session.commit()` inside the
block raises `RuntimeError` because the outermost block owns the commit.

For partial success, put SQLAlchemy's `session.begin_nested()` **outside** each
inner commit bracket and catch the row exception outside its savepoint:

```python
    from sqlalchemy.exc import IntegrityError

    async with self.shared_write_action_commit():
        for item in items:
            try:
                async with self.session.begin_nested():
                    await self.handle_create(item)
            except IntegrityError:
                continue
```

The savepoint contains the row's mutation and before-hook writes, so a failed
row rolls both back. Its queued after-hook is discarded. Surviving after-hooks
run in order after the shared commit and stop on the first exception.

The SaaS example runs both forms. `POST /tasks/bulk`, `/tasks/bulk-delete` and
`/tasks/import-csv` put a savepoint around each row inside one
`shared_write_action_commit()` block
([`app/tasks/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/tasks/views.py)). `POST /projects/{id}/clone`
commits a project, its outbox event, and the copied tasks together
([`app/projects/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/projects/views.py)).

### Relationship references in custom routes

When a custom route constructs schemas itself (`model_construct()` skips
validation), {class}`IDRef <fastapi_restly.schemas.IDRef>` fields need explicit wrapping; the recipe lives in
[Relationships](#idref-custom-routes).

## Exclude CRUD routes

Set {attr}`exclude_routes <fastapi_restly.views.BaseRestView.exclude_routes>` to prevent selected CRUD endpoint methods from being registered as routes:

```python
@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    exclude_routes = [fr.ViewRoute.DELETE, fr.ViewRoute.UPDATE]
```

Valid values are: {attr}`fr.ViewRoute.GET_MANY <fastapi_restly.views.ViewRoute.GET_MANY>`, {attr}`fr.ViewRoute.GET_ONE <fastapi_restly.views.ViewRoute.GET_ONE>`, {attr}`fr.ViewRoute.CREATE <fastapi_restly.views.ViewRoute.CREATE>`, {attr}`fr.ViewRoute.UPDATE <fastapi_restly.views.ViewRoute.UPDATE>`, {attr}`fr.ViewRoute.DELETE <fastapi_restly.views.ViewRoute.DELETE>`. Endpoint-method names such as `"delete_endpoint"` are also accepted; any other string raises `AttributeError` at startup.

## Choosing between `@fr.route` and the shorthand decorators

Prefer {func}`@fr.get <fastapi_restly.views.get>`, {func}`@fr.post <fastapi_restly.views.post>`, {func}`@fr.put <fastapi_restly.views.put>`, {func}`@fr.patch <fastapi_restly.views.patch>`, and {func}`@fr.delete <fastapi_restly.views.delete>` for most endpoints. They set the HTTP method automatically and apply Restly's default status codes: `@fr.get`/`@fr.put`/`@fr.patch` use 200, `@fr.post` uses 201, and `@fr.delete` uses 204.

Use {func}`@fr.route(path, methods=[...], ...) <fastapi_restly.views.route>` only when you need full manual control over route options, for example to register a single path under multiple HTTP methods or to set a non-standard response code:

```python
    @fr.route("/{id}/thumbnail", methods=["GET", "HEAD"], status_code=200)
    async def thumbnail(self, id: int):
        ...
```

Both `@fr.route` and the shorthand decorators pass their keyword arguments through to FastAPI's route registration. Class-based routes therefore use the same configuration surface as regular FastAPI routes, including `response_model=`, `status_code=`, `dependencies=`, `responses=`, `tags=`, and other `APIRouter.add_api_route()` options.

## What is available on `self`

Inside any method or custom route, the following attributes are always available:

| Attribute | Type | Description |
|---|---|---|
| {attr}`self.session <fastapi_restly.views.RestView.session>` | `AsyncSession` | The current database session |
| {attr}`self.request <fastapi_restly.views.BaseRestView.request>` | `fastapi.Request` | The live HTTP request |
| {attr}`self.model <fastapi_restly.views.BaseRestView.model>` | `type[DeclarativeBase]` | The SQLAlchemy model class |
| {attr}`self.schema <fastapi_restly.views.BaseRestView.schema>` | `type[pydantic.BaseModel]` | The view's schema, a Pydantic model |

Any class-level `Annotated` dependency you declare on the view (for example a current user) is also injected and available as an instance attribute; see [Dependency injection on class attributes](class_based_views.md#dependency-injection-on-class-attributes).

## See also

- [Views](class_based_views.md): why subclassable views make all
  of this possible.
- [Compose Views with Mixins](howto_compose_views_with_mixins.md): structural
  fields on the model, soft delete as a view mixin.
- [View Method Surface](#view-method-surface): the complete
  classified method list.
