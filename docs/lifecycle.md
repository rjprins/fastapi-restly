# Request Lifecycle & Hooks

Every CRUD route of a {class}`RestView <fastapi_restly.views.RestView>` or
{class}`AsyncRestView <fastapi_restly.views.AsyncRestView>` runs its steps
in a fixed order. A write route checks that the caller may act, makes the
change, commits it and builds the response. A read route also checks the
caller, but it only loads rows, so it has no commit. Each step except the
commit is a method on the view. To change a route, you override the method of
one step. A custom route calls the same methods, so it runs the same steps.

[CRUD Routes](crud_routes.md) lists the default routes and their settings.
[Views](views.md) covers what every view shares: registration,
dependencies and inheritance.

(view-request-steps)=
## How a view handles a request

Take `POST /`, the create route. FastAPI calls
{meth}`create_endpoint <fastapi_restly.views.RestView.create_endpoint>`, the
**endpoint method**. It calls
{meth}`handle_create <fastapi_restly.views.RestView.handle_create>`, the
**handler**. The handler runs the steps in this order:

```
POST /
  └─ create_endpoint(schema_obj)                 # endpoint method: the HTTP contract
       ├─ handle_create(schema_obj)              # handler: runs the steps (final)
       │    ├─ authorize("create", data=schema_obj)
       │    ├─ create(schema_obj)                 # business method: the change
       │    │    ├─ make_new_object(schema_obj)   # build the ORM object (final)
       │    │    └─ save_object(obj)              # flush and refresh, no commit (final)
       │    ├─ before_action_commit("create", new=obj)
       │    ├─ commit
       │    └─ after_action_commit("create", new=obj)
       └─ to_response(obj)
```

Every CRUD route has these three methods. The names follow one pattern for
all five actions: `update_endpoint` calls `handle_update`, which calls
`update`, and so on.

- The **endpoint method**, `<verb>_endpoint`, is the method that FastAPI
  calls. It owns the HTTP contract: the route decorator, the parameters, the
  `response_model` and the call to
  {meth}`to_response <fastapi_restly.views.BaseRestView.to_response>`.
  Replace it only to change that contract, such as the status code. See
  [Change the status code, headers or response of one route](#replace-endpoint-method).
- The **handler**, `handle_<verb>`, runs the steps. It calls
  {meth}`authorize <fastapi_restly.views.RestView.authorize>` and then the
  business method. On a write, it ends with the **commit bracket**:
  {meth}`before_action_commit <fastapi_restly.views.RestView.before_action_commit>`,
  the commit, then
  {meth}`after_action_commit <fastapi_restly.views.RestView.after_action_commit>`.
  It returns the object. Only the delete handler returns nothing. A handler
  is final: a view class that defines one fails when Python creates the
  class. A custom route calls a handler to run the same steps.
- The **business method** is the bare verb:
  {meth}`create <fastapi_restly.views.RestView.create>`,
  {meth}`update <fastapi_restly.views.RestView.update>`,
  {meth}`delete <fastapi_restly.views.RestView.delete>`,
  {meth}`get_one <fastapi_restly.views.RestView.get_one>` or
  {meth}`get_many <fastapi_restly.views.RestView.get_many>`. It makes the
  change: it builds the object, applies the input and saves. It does not check
  permissions and does not commit. So when your override runs, `authorize` has
  already passed, and the commit comes after it.

:::{admonition} Details
:class: details

Update and delete have the same steps, with a load first. Their handlers load
the row through {meth}`get_one <fastapi_restly.views.RestView.get_one>`, so a
row that the caller cannot see is a 404. Then they call `authorize` with the
loaded row and take a
{meth}`snapshot <fastapi_restly.views.BaseRestView.snapshot>` of it as `old`.
The business method runs next, then the commit bracket. The hooks receive both
`new` and `old`, so they can see what changed.
:::

(read-request-steps)=
### A read: visibility, then policy

A read has no commit bracket. Two steps decide whether the caller gets the
row:

```
GET /{id}
  └─ get_one_endpoint(id)            # endpoint method
       ├─ handle_get_one(id)         # handler
       │    ├─ get_one(id)           # business method
       │    │    └─ the view scope   # VISIBILITY: soft delete, role, row-level
       │    │                        #   a hidden row is a 404 for every caller
       │    └─ authorize("get_one", obj=obj)   # POLICY: checks the loaded row
       └─ to_response(obj)
```

- **Visibility.** The business method
  {meth}`get_one <fastapi_restly.views.RestView.get_one>` loads the row
  through the view's scope. A row outside the scope is a 404 for every caller,
  as if the row did not exist. See [Scopes](scopes.md).
- **Policy.** The handler then calls `authorize("get_one", obj=obj)` with the
  loaded row. A refusal is a 403. See [Authorization](authorization.md).

`get_one` itself does not call `authorize`: the handler does. A list has the
same two steps in the other order.
{meth}`handle_get_many <fastapi_restly.views.RestView.handle_get_many>` first
calls `authorize("get_many")`, with no row. Then
{meth}`get_many <fastapi_restly.views.RestView.get_many>` applies the scope
and the client's filters, sort and page
({meth}`apply_list_params <fastapi_restly.views.RestView.apply_list_params>`).
On a paginated view, it also counts the total
({meth}`count <fastapi_restly.views.RestView.count>`).

(which-method-to-override)=
## Which method do I override

| I want to change… | Use |
|---|---|
| What an action does on the HTTP routes: hash a password, check a rule | {meth}`create <fastapi_restly.views.RestView.create>` / {meth}`update <fastapi_restly.views.RestView.update>` / {meth}`delete <fastapi_restly.views.RestView.delete>` ([below](#business-methods)) |
| A value that every write path must set: a slug, the author | a column default or a mapper event on the model ([Models](#write-path-values)) |
| Who may perform an action | {meth}`authorize <fastapi_restly.views.RestView.authorize>` ([Authorization](#authorize-gate)) |
| Which rows a caller can see | {attr}`scope <fastapi_restly.views.BaseRestView.scope>` ([Scopes](#view-scope)) |
| The keys that a list has | {attr}`include_filters <fastapi_restly.views.BaseRestView.include_filters>` / {attr}`include_sort_keys <fastapi_restly.views.BaseRestView.include_sort_keys>` ([Choose the keys of a list](#choose-list-keys)) |
| A list filter that is not a comparison with a column | {func}`fr.list_filter <fastapi_restly.views.list_filter>` ([Add a filter of your own](#own-list-filter)) |
| How the list params filter, sort and page | {meth}`apply_list_params <fastapi_restly.views.RestView.apply_list_params>` ([Filtering & Pagination](filtering.md)) |
| The list total | {meth}`count <fastapi_restly.views.RestView.count>` |
| A side effect inside the commit: an outbox or audit row | {meth}`before_action_commit <fastapi_restly.views.RestView.before_action_commit>` ([below](#transaction-hooks)) |
| A side effect after the commit: an email, a webhook | {meth}`after_action_commit <fastapi_restly.views.RestView.after_action_commit>` ([below](#transaction-hooks)) |
| The response shape of every route | {meth}`to_response <fastapi_restly.views.BaseRestView.to_response>` ([Responses](#to-response)) |
| The HTTP contract of one route: status, headers, parameters | `<verb>_endpoint` ([below](#replace-endpoint-method)) |
| A path that is not CRUD: a lookup, an action such as publish | a method with {func}`@fr.get <fastapi_restly.views.get>` or {func}`@fr.post <fastapi_restly.views.post>` ([read route](#custom-read-route), [action route](#custom-action-route)) |
| A lookup by another key, such as a slug or an email | {meth}`handle_get_one <fastapi_restly.views.RestView.handle_get_one>` with an expression, in a custom route ([below](#natural-key-route)) |
| A list of a subset, such as one project's tasks | {meth}`handle_get_many <fastapi_restly.views.RestView.handle_get_many>` with `where=`, in a custom route ([Scopes](#per-read-where)) |
| One commit over several writes | {meth}`shared_write_action_commit <fastapi_restly.views.RestView.shared_write_action_commit>` in a custom route ([below](#shared-write-action-commit)) |
| The same change on several views | a base view or a mixin ([Views](#share-behaviour-with-base-views)) |

Start at the business method. Move a side effect that depends on the commit to
a hook. Replace the endpoint method only when the HTTP contract changes.

(business-methods)=
## Change what an action does

Override the business method of the action:
{meth}`create <fastapi_restly.views.RestView.create>`,
{meth}`update <fastapi_restly.views.RestView.update>`,
{meth}`delete <fastapi_restly.views.RestView.delete>`,
{meth}`get_one <fastapi_restly.views.RestView.get_one>` or
{meth}`get_many <fastapi_restly.views.RestView.get_many>`. Override only the
one you need. The handler still checks permissions and commits around it.

Before you set a value in `create` or `update`, ask which write paths must set
it, as [Set a value on every write path](#write-path-values) explains:

- A value that every write path must set belongs on the model, as a column
  default or a mapper event.
- A value that only the HTTP routes set belongs in `create` or `update`.

The common cases:

- **Hash a password:** in `create`. The plain password arrives in the request
  body, and only the route has it. A script that also gets plain passwords
  calls the same function: see
  [Run the same logic in a script or a worker](#logic-outside-a-view).
- **Make a slug from the title:** on the model. Every new row needs a slug,
  whichever path writes it.
- **Keep a total on a parent row up to date**, such as the story points of a
  project: on the model. The total is wrong as soon as one write path skips it.
- **Send an event about a change:** in a
  [transaction hook](#transaction-hooks), for an event about an action of the
  view. An event that every write path must send needs a SQLAlchemy session
  event instead.

(password-hash-example)=
### Hash a password in `create`

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
    password: fr.WriteOnly[str]  # accepted on input, never in a response


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

{meth}`handle_create <fastapi_restly.views.RestView.handle_create>` still calls
`authorize` and commits. The override only changes how the object is built.
The schema field and the column have the same name, because
{meth}`make_new_object <fastapi_restly.views.RestView.make_new_object>` passes
every writable schema field that the client sent to the model's constructor.
So the plain password goes into `password` first, and the override replaces
it with the hash before the flush. `fr.WriteOnly` keeps the field out of every response.

The generated update body takes `password` too. Without more code,
`PATCH /users/{id}` stores a new password as plain text. So hash it in
`update` as well:

```python
    async def update(self, obj, schema_obj):
        obj = await self.update_object(obj, schema_obj)
        if "password" in schema_obj.model_fields_set:
            obj.password = hash_password(schema_obj.password)
        return await self.save_object(obj)
```

{meth}`update_object <fastapi_restly.views.RestView.update_object>` writes
only the fields that the request sends, so the check uses the same set. The
other way is an [explicit update schema](#explicit-write-schemas) without
`password`, with a separate route that changes the password. The SaaS example
does this: its change-password route checks the current password first
([`app/users/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/users/views.py)).

### Refuse a change in `update`

{meth}`update <fastapi_restly.views.RestView.update>` receives the row that
{meth}`handle_update <fastapi_restly.views.RestView.handle_update>` has
already loaded through the scope, not the id. A rule that needs the stored row
or the database goes here:

```python
    async def update(self, obj, schema_obj):
        if obj.locked:
            raise fr.exc.Conflict("This record is locked")
        obj = await self.update_object(obj, schema_obj)
        return await self.save_object(obj)
```

A rule about who may make the change goes in
[`authorize`](#authorize-gate). A rule about one field's value is a Pydantic
validator on the schema: see [Validation](#validation).
[Errors](errors.md) lists the exceptions to raise.

### Add a value to each row of a list

To change the rows after the query, override
{meth}`get_many <fastapi_restly.views.RestView.get_many>` and call `super()`:

```python
    async def get_many(self, list_params, *, scope=None):
        result = await super().get_many(list_params, scope=scope)
        for project in result.objects:
            project.can_edit = project.owner_id == Current.user_id()
        return result
```

`Current.user_id()` is the id of the caller
([Request Context](context.md)). The view's schema declares the
field:

```python
    can_edit: fr.ReadOnly[bool | None] = None
```

It is not a column, so `ReadOnly` keeps it out of the create and update
bodies.

Other changes to a list have their own place. A [scope](#view-scope) hides
rows.
{meth}`apply_list_params <fastapi_restly.views.RestView.apply_list_params>`
changes how the list params filter, sort and page
([Filtering & Pagination](filtering.md)).
{meth}`get_relationship_loader_options <fastapi_restly.views.BaseRestView.get_relationship_loader_options>`
loads more relationships ([Relationships](#eager-load-extra-relationships)).

:::{admonition} Details
:class: details

The handlers always pass `scope=`, so the `get_many` override declares it and
passes it on. To change the single-row read too, override
{meth}`get_one <fastapi_restly.views.RestView.get_one>` the same way, as the
[SaaS example](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/projects/views.py)
does.
:::

(domain-utilities)=
## Build and save objects

The business methods are built from three utilities. Call them from your
`create` and `update` overrides. They are final: a view class that defines
one, itself or through a mixin, fails when Python creates the class.

| Method | What it does |
|---|---|
| {meth}`self.make_new_object(schema_obj) <fastapi_restly.views.RestView.make_new_object>` | Builds a new ORM object from the schema and adds it to the session. It resolves references and skips the fields that the schema marks read-only. It does not flush. |
| {meth}`self.update_object(obj, schema_obj) <fastapi_restly.views.RestView.update_object>` | Writes the writable fields that the client sent onto an existing object, and resolves references. It does not flush. |
| {meth}`self.save_object(obj) <fastapi_restly.views.RestView.save_object>` | Flushes and refreshes `obj`, then loads the relationships that the response class names. It does not commit. |

None of them commits. The handler, or a `write_action` block, commits.

The same operations are functions in {mod}`fr.objects <fastapi_restly.objects>`,
for code outside a view:
{func}`async_make_new_object <fastapi_restly.objects.async_make_new_object>`,
{func}`async_update_object <fastapi_restly.objects.async_update_object>`,
{func}`async_save_object <fastapi_restly.objects.async_save_object>` and
{func}`async_delete_object <fastapi_restly.objects.async_delete_object>`, and
their sync versions. See
[The same operations outside a view](#advanced-object-helpers). These
functions do not call `authorize` or the hooks. They only build, change, save
or delete.

(logic-outside-a-view)=
### Run the same logic in a script or a worker

While the view is the only caller, keep the logic in its `create` or `update`.
There it has `self.session`, `self.request` and the view's dependencies.

When a script or a worker must run the same logic, move it into a plain
function and call it from both:

```python
from fastapi_restly.objects import async_make_new_object, async_save_object


def set_password(user: User, raw_password: str) -> None:
    user.password = hash_password(raw_password)


class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserSchema

    async def create(self, schema_obj):
        user = await self.make_new_object(schema_obj)
        set_password(user, schema_obj.password)
        return await self.save_object(user)


async def import_user(session, payload) -> User:
    user = await async_make_new_object(session, User, payload)
    set_password(user, payload.password)
    await async_save_object(session, user)
    await session.commit()
    return user
```

The script owns its transaction, so it commits itself. Do not move logic out
of the view before there is a second caller.

A value that every write path must set does not need this. Put it on the
model ([Models](#write-path-values)), and every path gets it.

(what-commits)=
## What commits, and when

Restly commits for you, once for each write action. The handler of a write
route, or a {meth}`write_action <fastapi_restly.views.RestView.write_action>`
block in a custom route, runs `before_action_commit`, commits, and then runs
`after_action_commit`. Your business methods and hooks do not commit.

The session that Restly opens for a request does not commit when the request
ends. It rolls back and closes. So a custom route that writes must commit
through a handler or a `write_action` block. A route on a plain
{class}`View <fastapi_restly.views.View>` has neither, so it commits the
session itself. When a request ends with changes
that are not committed, Restly warns with
{class}`RestlyUncommittedChangesWarning <fastapi_restly.exc.RestlyUncommittedChangesWarning>`.

To commit several writes at once, see
[Commit several writes together](#shared-write-action-commit).

:::{admonition} Details
:class: details

The warning is on by default. To turn it off for the app, pass
`warn_on_uncommitted=False` to {func}`fr.configure <fastapi_restly.db.configure>`.
A route that rolls back on purpose, such as a dry run, can turn it off for one
request with `session.info["_fr_suppress_uncommitted"] = True`.

A [session generator of your own](#own-session-generator) decides how a
session is made and closed. It does not change who commits.
:::

(transaction-hooks)=
## Run code before or after the commit

Every write action calls two hooks around its commit:

- {meth}`before_action_commit(action, new, old=None) <fastapi_restly.views.RestView.before_action_commit>`
  runs inside the transaction, before the commit. What it writes is committed
  together with the change, or not at all. Use it for outbox rows and audit
  rows.
- {meth}`after_action_commit(action, new, old=None) <fastapi_restly.views.RestView.after_action_commit>`
  runs after the commit, when the change is saved. Use it for email, webhooks
  and cache invalidation.

The hooks run for the CRUD writes and for every `write_action` block. Reads do
not call them, and neither do the `fr.objects` functions. The arguments are:

- `action`: the name of the action, such as `"create"`, or the name that a
  custom route gives `write_action`. See [Action names](#action-names).
- `new`: the saved object. On a delete, it is `None`.
- `old`: a dict of the row's column values before the change, from
  {meth}`snapshot <fastapi_restly.views.BaseRestView.snapshot>`. It holds only
  the columns that were loaded. On a create, it is `None`.

With `old`, a hook can check what changed:

```python
    async def after_action_commit(self, action, new, old=None):
        if action == "update" and old["status"] != new.status:
            await notify_status_change(new.id, new.status)
```

Do not change `new` or the database in `after_action_commit`. The commit is
done, so the change is not saved. A change to `new` still shows in the
response of this request, which hides the problem. Make the change in the
business method or in `before_action_commit`.

:::{admonition} Details
:class: details

On a delete, `new` is `None` and `old` holds the columns of the removed row. A
delete that must finish outside the request can mark the row in `delete`, as a
[soft delete](#soft-delete-recipe) does. After the commit,
`after_action_commit` starts the real delete:

```python
    async def delete(self, obj):
        obj.status = "pending_deletion"
        await self.session.flush()  # no super(): the row stays

    async def after_action_commit(self, action, new, old=None):
        if action == "delete":
            await enqueue_async_delete(old["id"])  # a worker deletes the row
```
:::

(after-action-commit-raises)=
### When `after_action_commit` raises

The write is already committed, so an exception from the hook cannot undo it.
The request still fails, and a client that tries again repeats the write. For
an effect that may fail without harm, catch and log the error in the hook:

```python
    async def after_action_commit(self, action, new, old=None):
        if action == "create":
            try:
                await send_confirmation_email(new.id)
            except Exception:  # the order is saved; don't fail the request
                logger.exception("Confirmation email failed for order %s", new.id)
```

An effect that must happen belongs in an outbox row, written in
`before_action_commit`. A worker then delivers it and tries again when it
fails. Make the delivery safe to repeat, because a retry can deliver it twice.
FastAPI's `BackgroundTasks` does not replace an outbox: a crash loses the
task, and nothing tries it again.

(replace-endpoint-method)=
## Change the status code or headers of one route

Business methods and hooks keep the HTTP contract of the CRUD route. Replace
the endpoint method when the contract itself must change: the status code,
the headers or the parameters. To change the shape of the response body, such
as an envelope around one object, see
[Change the shape of one route](#one-route-shape) on Responses. It replaces
the endpoint method in the same way.

Define a method with the same name, such as `delete_endpoint`, and give it a
route decorator. Most replacements call the handler and only change the
response:

```python
@fr.include_view(app)
class ProductView(fr.AsyncRestView):
    prefix = "/products"
    model = Product
    schema = ProductSchema

    @fr.delete("/{id}", status_code=200)
    async def delete_endpoint(self, id: int):
        obj = await self.get_one(id)               # load through the scope, or 404
        serialized = self.to_single_response(obj).model_dump(mode="json")
        await self.handle_delete(id)               # authorize, delete, commit
        return serialized
```

The default `DELETE /{id}` answers `204 No Content`. This version answers
`200` with the deleted row, as React Admin's `ra-data-simple-rest` expects
([React Admin](react_admin.md)).

:::{admonition} Details
:class: details

When Restly registers the view, an endpoint method that the class defines
replaces the inherited method with the same name. The other inherited
endpoint methods stay.

The built-in endpoint methods get the view's types from markers, such as
`get_one_endpoint(self, id: ViewID) -> ViewResponse`. A replacement gets the
view's types only where it uses the markers too; see
[Name the view's classes in a custom route](#name-the-response-classes). A
parameter or a return without an annotation gets no type. Restly keeps an
annotation that you write, so `schema_obj: ProductReplace` validates the
request body against `ProductReplace`. `list_params` is the exception: it
always gets the view's
{attr}`schema_list_params <fastapi_restly.views.BaseRestView.schema_list_params>`.
:::

### Change the list route

Replace
{meth}`get_many_endpoint <fastapi_restly.views.RestView.get_many_endpoint>`
when the contract of the list changes, for example to send the total in a
header. Keep the `list_params` parameter to keep Restly's
[filter, sort and page parameters](filtering.md):

```python
import fastapi
import json

from fastapi_restly.views import ViewResponse


@fr.include_view(app)
class ProductView(fr.AsyncRestView):
    prefix = "/products"
    model = Product
    schema = ProductSchema

    @fr.get("/", response_model=list[ViewResponse])
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

The route sends a plain list, not the default envelope, so it names its
`response_model`: a list of the view's response class. Without it, OpenAPI
shows no schema for the response.
[Name the view's classes in a custom route](#name-the-response-classes)
explains the markers, such as `ViewResponse`.

### Share a changed route across views

When several views need the same contract, put the replacement in a mixin.
List the mixin before the view class, so Python finds its method first:

```python
from fastapi_restly.views import ViewID, ViewResponse


class DeleteReturnsObjectMixin:
    @fr.delete("/{id}", status_code=200)
    async def delete_endpoint(self, id: ViewID) -> ViewResponse:
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

`ViewID` and `ViewResponse` give each view that uses the mixin its own id
type and response class.

The [React Admin](react_admin.md) views use this pattern. They replace
{meth}`get_many_endpoint <fastapi_restly.views.RestView.get_many_endpoint>`
for the `ra-data-simple-rest` contract, and keep the handlers and business
methods.

(custom-read-route)=
## Add a custom read route

A CRUD view can have more routes beside its CRUD routes. Declare a method with
{func}`@fr.get <fastapi_restly.views.get>`, as on any view
([Views](#register-a-view)). To load one row, call
{meth}`handle_get_one <fastapi_restly.views.RestView.handle_get_one>`. It
loads the row through the scope, so a row that the caller cannot see is a
404. Then it calls `authorize("get_one", obj=row)`, as `GET /{id}` does.
{meth}`get_one <fastapi_restly.views.RestView.get_one>` alone also loads
through the scope, but it does not call `authorize`.

The SaaS example's `GET /users/me` returns the caller's own row:

```{literalinclude} ../example-projects/saas/app/users/views.py
:language: python
:pyobject: UserView.get_current_user
```

`Current.user_id()` is the id of the caller
([Request Context](context.md)).

:::{admonition} Details
:class: details

`UserResponse` is the view's response class, so OpenAPI shows the same type
as for `GET /users/{id}`. See
[Name the view's classes in a custom route](#name-the-response-classes). The
route `/me` works beside `/{id}`, because the item routes match only an
integer id ([Item paths and static routes](#item-route-matching)).

The handlers return the ORM object, so a route can read every attribute of
the model, also the ones that the response leaves out. In the
[same example](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/projects/views.py),
`GET /projects/{id}/stats` calls `handle_get_one(id)`, and then counts the
project's tasks.
:::

(natural-key-route)=
### Look a row up by another key

{meth}`handle_get_one <fastapi_restly.views.RestView.handle_get_one>` and
{meth}`get_one <fastapi_restly.views.RestView.get_one>` take a SQLAlchemy
boolean expression in place of the id. The expression picks the row instead
of the primary key. Everything else about the load stays the same: the view's
[scope](#view-scope), the relationships that the response loads, and the 404.

```python
    @fr.get("/by-email/{email}", response_model=UserResponse)
    async def get_by_email(self, email: str):
        user = await self.handle_get_one(User.email == email)
        return self.to_response(user)
```

:::{admonition} Details
:class: details

Combine columns with `sqlalchemy.and_(...)`. This is also how a custom route
reaches a row of a model with a composite primary key. A plain id cannot name
such a row, and passing one raises `NotImplementedError`.

The expression narrows the read inside the scope. It never reaches past it,
so a row that the view hides stays a 404 under any key. Only
{attr}`scope <fastapi_restly.views.BaseRestView.scope>` changes what the view
sees. When more than one row matches, Restly raises SQLAlchemy's
`MultipleResultsFound` and does not return the first row: a key that matches
several rows is a bug in the expression.

{meth}`handle_update <fastapi_restly.views.RestView.handle_update>` and
{meth}`handle_delete <fastapi_restly.views.RestView.handle_delete>` take the
same expression. So a custom `PATCH /users/by-email/{email}` route that calls
`handle_update(User.email == email, schema_obj)` runs the same steps as
`PATCH /users/{id}`. The SaaS example reads and updates projects by slug in
[`app/projects/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/projects/views.py).
:::

(custom-action-route)=
## Add a custom action route

An action changes state: publish, archive, recalculate. Declare it with
{func}`@fr.post <fastapi_restly.views.post>`, or with
{func}`@fr.patch <fastapi_restly.views.patch>` or
{func}`@fr.delete <fastapi_restly.views.delete>`. Most actions take one of
three forms.

**Change one row in a `write_action` block.** Load the row with
{meth}`get_one <fastapi_restly.views.RestView.get_one>`. Then change it inside
{meth}`write_action <fastapi_restly.views.RestView.write_action>`, under the
name of the action. The blog example publishes a post:

```{literalinclude} ../example-projects/blog/blog/main.py
:language: python
:pyobject: PostView.publish
```

When the block starts, Restly calls `authorize("publish", obj=post)` and takes
the snapshot of the row. When the block ends, Restly runs
`before_action_commit`, commits, and runs `after_action_commit`, each with the
action name `"publish"`. If the block raises, as it does here for a post that
is already published, nothing is committed. The route loads with `get_one`,
not with `handle_get_one`, because `write_action` authorizes the action
itself. [Authorize a custom action](#authorize-custom-actions) shows how
`authorize` handles a new name.

The SaaS example uses this form for the task changes `POST /tasks/{id}/start`,
`complete` and `reopen`
([`app/tasks/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/tasks/views.py)),
and for `POST /users/{id}/change-password`
([`app/users/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/users/views.py)).

**Run a CRUD action under another path.** When the action is a create or an
update with another path or another body, call the handler,
{meth}`handle_create <fastapi_restly.views.RestView.handle_create>` or
{meth}`handle_update <fastapi_restly.views.RestView.handle_update>`. The
route then runs every step of that CRUD action, with `authorize` and the
commit. The SaaS example's `PATCH /users/me` updates the caller's own row:

```python
    @fr.patch("/me", response_model=UserResponse)
    async def update_current_user(self, request: UpdateMeRequest) -> Any:
        user = await self.handle_update(Current.user_id(), request)
        return self.to_single_response(user)
```

`UpdateMeRequest` has only `name` and `email`, so this route cannot change
other fields.
{meth}`update_object <fastapi_restly.views.RestView.update_object>` writes
the fields of the schema it receives, and only the fields that the client
sent. A change to the `update` business method applies here too.

**Create in a block of your own.** Some creates need more than `create` does,
such as a parent row and its child rows. Use a `write_action` block without
`obj`, and set the new object on the handle before the block ends:

```python
    async with self.write_action("create", data=schema_obj) as w:
        obj = await self.make_new_object(schema_obj)
        w.obj = await self.save_object(obj)
    return self.to_response(w.obj)
```

The hooks receive `w.obj` as `new`. If the block ends without it, Restly
raises `RuntimeError`. For a write that has no single object, pass `obj=None`
instead. The SaaS example's `POST /uploads` saves an upload and its lines this
way
([`app/uploads/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/uploads/views.py)).
`POST /task-labels/create-and-attach` saves a label and its link to a task in
one block
([`app/labels/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/labels/views.py)).

:::{admonition} Details
:class: details

Name the view's response classes in `response_model`, so OpenAPI shows the
same types as for the CRUD routes: see
[Name the view's classes in a custom route](#name-the-response-classes). A route
that builds a schema with `model_construct()` skips validation, so its
reference fields need the right form: see
[Build references in a custom route](#idref-custom-routes).
:::

(shared-write-action-commit)=
## Commit several writes together

Each handler and each `write_action` block commits on its own. To commit
several of them at once, put them inside a
{meth}`shared_write_action_commit() <fastapi_restly.views.AsyncRestView.shared_write_action_commit>`
block. The outermost block commits once. Then the queued `after_action_commit`
hooks run:

```python
    @fr.post("/bulk", status_code=201)
    async def bulk_create(self, items: list[OrderCreate]):
        async with self.shared_write_action_commit():
            orders = [await self.handle_create(item) for item in items]
        return [self.to_response(order) for order in orders]
```

Inside the block, each action still calls `authorize`, takes the snapshot,
makes its change and runs `before_action_commit`. Then it flushes and returns.
Its changes are not committed yet, and its `after_action_commit` has not run.
So build the responses after the block, as in the example. Do the same for
code that needs an after-hook to have run. On `fr.RestView`, use `with` and
the sync handlers. The method has the same name.

:::{admonition} Details
:class: details

The block can also hold other writes, for an operation on more than one
model:

```python
    async with self.shared_write_action_commit():
        project = await self.handle_create(project_data)
        self.session.add_all(
            Task(project_id=project.id, title=task.title)
            for task in source_tasks
        )
    return self.to_response(project)
```

The new project, the rows that its `before_action_commit` writes, and the
copied tasks are committed together. If any part fails, none of them is
committed.

Blocks inside each other share the commit when their views share a session.
An exception that leaves a block cancels the shared commit and drops the
queued hooks. This also happens when an outer block catches the exception.
Rolling back stays the job of the session's owner. A direct `session.commit()`
inside the block raises `RuntimeError`, because the outermost block owns the
commit.

When some rows may fail and the others must still be saved, put SQLAlchemy's
`session.begin_nested()` around each handler call. Catch the error outside
that savepoint:

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

The savepoint holds the row's change and the rows that its
`before_action_commit` wrote. So a failed row rolls back both, and its queued
after-hook is dropped. Inside the shared block, the row's error stays a raw
`IntegrityError`, so the route can catch it. An `IntegrityError` that escapes
the shared block, or that its commit raises, becomes a `409`; see
[Unique values and other database conflicts: 409](#default-exception-handling). After the shared commit, the after-hooks of the saved
rows run in order. They stop at the first exception.

The SaaS example runs both forms. `POST /tasks/bulk`, `/tasks/bulk-delete`
and `/tasks/import-csv` put a savepoint around each row inside one
`shared_write_action_commit()` block
([`app/tasks/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/tasks/views.py)).
`POST /projects/{id}/clone` commits a project, its outbox event and the copied
tasks together
([`app/projects/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/projects/views.py)).
:::

## What is available on `self`

Inside every method and custom route, you can use these attributes:

| Attribute | Type | Description |
|---|---|---|
| {attr}`self.session <fastapi_restly.views.RestView.session>` | `AsyncSession`, or `Session` on `RestView` | The database session of the request |
| {attr}`self.request <fastapi_restly.views.BaseRestView.request>` | `fastapi.Request` | The HTTP request |
| {attr}`self.model <fastapi_restly.views.BaseRestView.model>` | the model class | The SQLAlchemy model of the view |
| {attr}`self.schema <fastapi_restly.views.BaseRestView.schema>` | `type[pydantic.BaseModel]` | The view's schema |

A class attribute that you declare with `Annotated[..., Depends(...)]`, such
as the current user, is also set on `self` for each request. See
[Declare dependencies as class attributes](#dependency-injection-on-class-attributes).

## Internals

The CRUD handlers and `write_action` share one implementation of the commit
bracket: {func}`async_run_write_action <fastapi_restly.views.async_run_write_action>`
and {func}`run_write_action <fastapi_restly.views.run_write_action>`. Each one
calls `authorize`, takes the snapshot, runs a function that makes the change,
and then runs `before_action_commit`, the commit and `after_action_commit`.
Inside a `shared_write_action_commit()` block, it flushes instead of
committing and queues the after-hook for the outermost block.

## See also

- [Authorization](authorization.md): who may perform each action.
- [Scopes](scopes.md): which rows a view can see.
- [Views](views.md): registration, dependencies and base views.
- [Models](#write-path-values): values that every write path must set.
- [View Reference](#view-method-surface): every view method, with its
  signature.
