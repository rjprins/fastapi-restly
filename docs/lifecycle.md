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

:::{call-tree}
- `POST /`
  - {override}`create_endpoint(data) <fastapi_restly.views.RestView.create_endpoint>`
    The endpoint method: the HTTP contract.
    - {meth}`handle_create(data) <fastapi_restly.views.RestView.handle_create>`
      The handler: it runs the steps. It is final.
      - {override}`authorize("create", data=data) <fastapi_restly.views.RestView.authorize>`
      - Check the [references](#reference-exists) in the data. An id that does
        not exist is a 404.
      - {override}`create(data) <fastapi_restly.views.RestView.create>`
        The business method: it builds the object. Nothing is written yet.
      - Save: flush, refresh the object, and load it with the loader options.

        The save does what `session.flush()` and `session.refresh()` do in
        plain SQLAlchemy. It flushes the session. Then it refreshes the object,
        and every other row of the view's model that the action wrote. It loads
        them with the view's
        [loader options](#eager-load-extra-relationships), as a read does: the
        relationships that the response shows, and any value that you add
        there. So the hooks and the response see the saved rows. Rows of other
        models are written, but not refreshed.
      - {override}`before_action_commit("create", new=obj, old=None) <fastapi_restly.views.RestView.before_action_commit>`
      - Commit.
      - {override}`after_action_commit("create", new=obj, old=None) <fastapi_restly.views.RestView.after_action_commit>`
    - {override}`to_single_response(obj) <fastapi_restly.views.BaseRestView.to_single_response>`
      The response of the object.
:::

A method marked *override* is a method that you can override in your view.
A step with an arrow opens and closes when you click it.

Every CRUD route has these three methods. The names follow one pattern for
all five actions: `update_endpoint` calls `handle_update`, which calls
`update`, and so on. A read has a load in the place of the business method.

- The **endpoint method**, `<verb>_endpoint`, is the method that FastAPI
  calls. It owns the HTTP contract: the route decorator, the parameters and
  the `response_model`. It returns the row, and Restly builds the response with
  {meth}`to_single_response <fastapi_restly.views.BaseRestView.to_single_response>`.
  To change one option of the route, such as the status code, use
  {func}`fr.modify_route <fastapi_restly.views.modify_route>`. Replace the method
  only when the change needs code. See
  [Replace the endpoint method of one route](#replace-endpoint-method).
- The **handler**, `handle_<verb>`, runs the steps. It calls
  {meth}`authorize <fastapi_restly.views.RestView.authorize>`, checks the
  [references](#reference-exists) in the data, and then calls the business
  method. On a write, it ends with the **commit bracket**: it saves the
  object, runs
  {meth}`before_action_commit <fastapi_restly.views.RestView.before_action_commit>`,
  commits, then runs
  {meth}`after_action_commit <fastapi_restly.views.RestView.after_action_commit>`.
  It returns the object. Only the delete handler returns nothing. A handler
  is final: a view class that defines one fails when Python creates the
  class. A custom route calls a handler to run the same steps.
- The **business method** of a write is the bare verb:
  {meth}`create <fastapi_restly.views.RestView.create>`,
  {meth}`update <fastapi_restly.views.RestView.update>` or
  {meth}`delete <fastapi_restly.views.RestView.delete>`. It makes the
  change: it builds the object or applies the input to it, and returns it. It
  does not check permissions, does not flush and does not commit. So when your
  override runs, `authorize` and the reference checks have already passed.
  Nothing is written while it runs. The save after it writes the change, and
  then the commit follows.
- A read has a **load**:
  {meth}`get_one <fastapi_restly.views.RestView.get_one>` or
  {meth}`get_many <fastapi_restly.views.RestView.get_many>`. A load is
  final, as a handler is. You change it through the scope, the list params,
  the loader options and two small hooks. See [A read](#read-request-steps).

:::{admonition} Details
:class: details

Update and delete have the same steps, with a load first. Their handlers load
the row through {meth}`get_one <fastapi_restly.views.RestView.get_one>`, so a
row that the caller cannot see is a 404. Then they call `authorize` with the
loaded row, check the references in the data, and take a
{meth}`snapshot <fastapi_restly.views.BaseRestView.snapshot>` of it as `old`.
The business method runs next, then the commit bracket. The delete business
method only marks the row for deletion.

The row that an action is about is its **subject**: the object that `create`
or `update` returns, or the row that `delete` gets. `old` is the subject
before the action, and `new` is the subject after it. So the hooks can see
what changed. After a delete, the row is gone and `new` is `None`. A soft
delete keeps the row, so `new` is the row.

The business method runs under SQLAlchemy's
{attr}`session.no_autoflush <sqlalchemy.orm.Session.no_autoflush>`.
So a query inside it does not write a half-built object, also on a
{class}`RestView <fastapi_restly.views.RestView>`, whose session flushes
before each query everywhere else.
:::

(read-request-steps)=
### A read: visibility, then policy

A read has no commit bracket and no business method. Its load is final. Each
step of the load comes from a declaration or from a small hook:

```{code-block} text
:emphasize-lines: 2,7,8,9,10,13,15,18,19,20,21

GET /{id}
  └─ get_one_endpoint(id)                     # endpoint method
       ├─ handle_get_one(id)                  # handler
       │    ├─ get_one(id)                    # the load (final)
       │    │    ├─ select()                  # select(model), the scope, the loader options
       │    │    │                            #   VISIBILITY: a hidden row is a 404 for every caller
       │    │    ├─ .where(where_id(id))      # hook: the primary key by default
       │    │    └─ no row: raise not_found(id)   # hook: a 404 by default
       │    └─ authorize("get_one", obj=obj)  # POLICY: checks the loaded row
       └─ to_single_response(obj)             # the response of the row

GET /
  └─ get_many_endpoint(list_params)
       ├─ handle_get_many(list_params)
       │    ├─ authorize("get_many")          # POLICY: no row yet
       │    └─ get_many(list_params)          # the load (final)
       │         ├─ select()                  # the same start as get_one
       │         ├─ apply_list_params(stmt, list_params)   # filters, sort, page
       │         └─ count(stmt)               # on a paginated view
       └─ to_list_response(result)
            └─ to_single_response(row)        # for each row
```

The highlighted lines are the methods that you can override in your view.

- **Visibility.** The load starts from
  {meth}`select() <fastapi_restly.views.BaseRestView.select>`: the model with
  the view's scope. A row outside the scope is a 404 for every caller, as if
  the row did not exist. See [Scopes](scopes.md).
- **Policy.** The handler calls `authorize`. For one row, it gets the loaded
  row, and a refusal is a 403. See [Authorization](authorization.md).

`PATCH /{id}`, `DELETE /{id}` and your action routes load their row with the
same `get_one`. So the scope,
{meth}`where_id <fastapi_restly.views.BaseRestView.where_id>` and
{meth}`not_found <fastapi_restly.views.BaseRestView.not_found>` apply to them
too.

:::{admonition} Details
:class: details

The loads are final: a view class that defines `get_one`, `get_many` or
`select` fails when Python creates the class. The message names the place
for each change. An override of a load could leave out the scope, or the
`scope=` that a route asked for, and nothing would tell you. With final
loads, the scope always applies.

`select()` also adds the options of
{meth}`get_loader_options <fastapi_restly.views.BaseRestView.get_loader_options>`,
so the rows are ready for the response. A custom read starts from it too, as
in `self.select().where(Item.price > 10)`. In a count, a subquery or a select
of some columns, SQLAlchemy leaves the loader options out.
:::

(update-route)=
## Update a row

`PATCH /{id}` changes one row. The client sends only the fields that it
wants to change, and the other fields keep their values. These steps run:

```{code-block} text
:emphasize-lines: 2,5,8,10,12,13

PATCH /{id}
  └─ update_endpoint(id, data)              # endpoint method
       ├─ handle_update(id, data)           # handler (final)
       │    ├─ get_one(id)                  # loads the row through the scope, or 404
       │    ├─ authorize("update", obj=obj, data=data)   # or 403
       │    ├─ check the references in data # an id that does not exist: 404
       │    ├─ old = snapshot(obj)          # the column values before the change
       │    ├─ update(obj, data)            # business method: the change
       │    ├─ save                         # flush and refresh the row
       │    ├─ before_action_commit("update", new=obj, old=old)
       │    ├─ commit
       │    └─ after_action_commit("update", new=obj, old=old)
       └─ to_single_response(obj)           # the response
```

The highlighted lines are the methods that you can override. Before
`update_endpoint` runs, FastAPI checks the request body against the view's
{attr}`update_schema <fastapi_restly.views.BaseRestView.update_schema>`. A
body that does not fit returns 422, and none of the steps run.

(update-what-to-change)=
### What you can change

The table starts with rules on the model and on the schema. They need less
code than an override, and they also hold for the other routes.

| Your change | Where it goes |
|---|---|
| The fields that a client can send | `fr.ReadOnly` on the main schema ([Hide a field](#readonly-and-writeonly)), or an update schema of your own ([Write the update schema yourself](#explicit-write-schemas)) |
| A rule on one value, such as a format or a range | A Pydantic validator on the schema ([Validate input](#validation)) |
| The rows that a client can update | The view's {attr}`scope <fastapi_restly.views.BaseRestView.scope>`. A row outside it is a 404 ([Scopes](#view-scope)) |
| Who may update a row | {meth}`authorize <fastapi_restly.views.RestView.authorize>`. A refusal is a 403 ([Authorization](#authorize-gate)) |
| How the route finds its row, such as by a slug | {meth}`where_id <fastapi_restly.views.BaseRestView.where_id>` ([Look a row up by another key](#natural-key-route)) |
| A value that every write sets, such as the user who changed the row last | `onupdate` on the column, or a mapper event ([Models](#write-path-values)) |
| What the update does: refuse a change, check two fields together, hash a password | {meth}`update <fastapi_restly.views.RestView.update>` ([below](#update-method)) |
| Work in the same transaction, such as an audit row | {meth}`before_action_commit <fastapi_restly.views.RestView.before_action_commit>` ([Run code before or after the commit](#transaction-hooks)) |
| Work after the commit, such as an email | {meth}`after_action_commit <fastapi_restly.views.RestView.after_action_commit>` ([Run code before or after the commit](#transaction-hooks)) |
| A value in the response that is not a column | {meth}`to_single_response <fastapi_restly.views.BaseRestView.to_single_response>` ([Add a value to each row](#value-on-each-row)) |
| The status code, the path or the dependencies of the route | {func}`fr.modify_route <fastapi_restly.views.modify_route>` ([Change one option of a route](#modify-route)) |
| The HTTP method, the parameters or the request body | Your own `update_endpoint` ([below](#replace-update-endpoint)) |
| No update route | `update_endpoint = None` ([Leave out a route](#leave-out-routes)) |
| The same update under another path, such as `PATCH /users/me` | A custom route that calls {meth}`handle_update <fastapi_restly.views.RestView.handle_update>` ([below](#update-from-custom-route)) |

(update-method)=
### Change what the update does

Override {meth}`update <fastapi_restly.views.RestView.update>`. It gets the
row and the request body. It changes the row and returns it. Call
`super().update()` to write the fields that the client sent:

```python
from fastapi import HTTPException


@fr.include_view(app)
class EventView(fr.AsyncRestView):
    prefix = "/events"
    model = Event

    async def update(self, obj, data):
        if obj.locked:
            raise fr.exc.Conflict("This event is locked")
        event = await super().update(obj, data)
        if event.ends_on < event.starts_on:
            raise HTTPException(422, "The event ends before it starts")
        return event
```

The first check reads the row as it is in the database. The second check
reads the row after the change. So it also works when the client sends only
one of the two dates. A Pydantic validator cannot do this check, because it
sees only the fields in the request body.

When `update` runs, you can rely on these things:

- `obj` is the row. Restly loaded it through the view's scope, and
  `authorize` allowed the update.
- `data` is the request body, an instance of the update schema. Pydantic has
  validated it. `data.model_fields_set` holds the names of the fields that
  the client sent.
- Every row that `data` refers to by its id exists; see
  [Check that the referenced row exists](#reference-exists).
- Nothing is written to the database yet.
- You can use `self.session`, `self.request` and the view's dependencies, as
  in every method of the view.

The default `update` writes each field that the client sent onto the row,
except the `ReadOnly` fields. It does not flush and does not commit. After
`update` returns the row, the handler saves it, runs `before_action_commit`,
commits and runs `after_action_commit`. When `update` raises, nothing is saved, and the client gets the
error. [Errors](errors.md) lists the exceptions that you can raise.

:::{admonition} Details
:class: details

`update` runs for `PATCH /{id}`, for a custom route that calls
`handle_update`, for the `PUT` route of a
[React Admin view](#react-admin-put), and in a `write_action` block that
calls `self.update()`. So a rule in `update` holds for all of them. A script or a
worker does not run it; see
[Run the same logic in a script or a worker](#logic-outside-a-view).

Nothing is written while `update` runs, so a query inside it sees the rows as
they are in the database, without your change. To make a query see the
change, call `await self.session.flush()` first.
[The new object has no id in `create`](#no-id-in-create) explains this for
`create`, and it is the same for `update`.

Restly takes the snapshot `old` before `update` runs. So
`before_action_commit` and `after_action_commit` can compare the values before
and after the change; see
[Run code before or after the commit](#transaction-hooks).

When a client sends `null` for a field whose type allows `None`, the column
becomes `NULL`. A field that the client leaves out keeps its value.

The generated update schema takes every field that the main schema does not
mark `ReadOnly`, also a `WriteOnly` field such as a password. To hash a new
password in `update`, see [Hash a password in `create`](#password-hash-example).

`update` must return the row. When it returns `None`, Restly raises
`TypeError`.
:::

(replace-update-endpoint)=
### Replace `update_endpoint`

To change one option of the route, such as its status code, use
{func}`fr.modify_route <fastapi_restly.views.modify_route>`. When the change
needs code, define `update_endpoint` in your view, with a route decorator.
Keep the call to `handle_update`, so the load, `authorize`, your `update` and
the commit still run. This view takes `PUT` with all fields, in place of
`PATCH`:

```python
from fastapi_restly.views import IDType, ResponseSchema


class ProductReplace(fr.BaseSchema):
    name: str
    price: float


@fr.include_view(app)
class ProductView(fr.AsyncRestView):
    prefix = "/products"
    model = Product

    @fr.put("/{id}")
    async def update_endpoint(self, id: IDType, data: ProductReplace) -> ResponseSchema:
        return await self.handle_update(id, data)
```

The view now has a `PUT /products/{id}` route, and no `PATCH` route. A body
without `name` or `price` returns 422. `IDType` and `ResponseSchema` stand
for the id type and the response schema of the view, so the path and the
response are the same as on the default route; see
[Name the view's schemas in a custom route](#name-the-view-schemas).

:::{admonition} Details
:class: details

To keep `PATCH` and add `PUT`, give the `PUT` route another method name, such
as `replace`. It is then a custom route next to the default one; see
[Update a row from a custom route](#update-from-custom-route).

A method named `update_endpoint` without a route decorator is not a route,
so the view then has no update route.
[Replace the endpoint method of one route](#replace-endpoint-method) has the
rules for all five routes.
:::

(update-from-custom-route)=
### Update a row from a custom route

{meth}`handle_update <fastapi_restly.views.RestView.handle_update>` runs all
the steps of `PATCH /{id}`. Call it from a route of your own to update a row
under another path. It takes the id of the row, or a SQLAlchemy expression
that picks the row:

```python
from fastapi_restly.views import ResponseSchema


class UpdateMeRequest(fr.BaseSchema):
    name: str | None = None
    email: str | None = None


@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User

    @fr.patch("/me")
    async def update_me(self, data: UpdateMeRequest) -> ResponseSchema:
        return await self.handle_update(Current.user_id(), data)

    @fr.patch("/by-email/{email}")
    async def update_by_email(self, email: str, data: UpdateMeRequest) -> ResponseSchema:
        return await self.handle_update(User.email == email, data)
```

`UpdateMeRequest` has only the fields `name` and `email`, so these routes
cannot change other fields. The scope, `authorize`, your `update` and the
commit run as on `PATCH /users/{id}`. `Current.user_id()` is the id of
the caller; see [Request Context](context.md).

A change that is more than new values for some fields, such as publishing a
post, is an action of its own; see
[Add a custom action route](#custom-action-route).

(which-method-to-override)=
## Which method do I override

| I want to change… | Use |
|---|---|
| What an action does on the HTTP routes: hash a password, check a rule | {meth}`create <fastapi_restly.views.RestView.create>` / {meth}`update <fastapi_restly.views.RestView.update>` / {meth}`delete <fastapi_restly.views.RestView.delete>` ([below](#business-methods)) |
| A value that every write path must set: a slug, the author | a column default or a mapper event on the model ([Models](#write-path-values)) |
| Who may perform an action | {meth}`authorize <fastapi_restly.views.RestView.authorize>` ([Authorization](#authorize-gate)) |
| Which rows a caller can see | {attr}`scope <fastapi_restly.views.BaseRestView.scope>` ([Scopes](#view-scope)) |
| How the item routes find their row: a slug, a composite key | {meth}`where_id <fastapi_restly.views.BaseRestView.where_id>` ([below](#natural-key-route)) |
| The error for a row that does not exist | {meth}`not_found <fastapi_restly.views.BaseRestView.not_found>` ([below](#natural-key-route)) |
| A value on each row of the response | {meth}`to_single_response <fastapi_restly.views.BaseRestView.to_single_response>`, or SQL on the model ([below](#value-on-each-row)) |
| What each row loads: a relationship, a SQL expression | {meth}`get_loader_options <fastapi_restly.views.BaseRestView.get_loader_options>` ([Relationships](#eager-load-extra-relationships)) |
| A read from another source: a cache, a service | the endpoint method, `get_one_endpoint` or `get_many_endpoint` ([below](#value-on-each-row)) |
| The keys that a list has | {attr}`include_filters <fastapi_restly.views.BaseRestView.include_filters>` / {attr}`include_sort_keys <fastapi_restly.views.BaseRestView.include_sort_keys>` ([Choose the keys of a list](#choose-list-keys)) |
| A list filter that the generated keys do not give | {func}`fr.list_filter <fastapi_restly.views.list_filter>` ([Add a filter of your own](#own-list-filter)) |
| How the list params filter, sort and page | {meth}`apply_list_params <fastapi_restly.views.RestView.apply_list_params>` ([Filtering, Sorting & Pagination](filtering.md)) |
| The list total | {meth}`count <fastapi_restly.views.RestView.count>` |
| A side effect inside the commit: an outbox or audit row, or other work that needs the new id | {meth}`before_action_commit <fastapi_restly.views.RestView.before_action_commit>` ([below](#transaction-hooks)) |
| A side effect after the commit: an email, a webhook | {meth}`after_action_commit <fastapi_restly.views.RestView.after_action_commit>` ([below](#transaction-hooks)) |
| How one object becomes a response | {meth}`to_single_response <fastapi_restly.views.BaseRestView.to_single_response>` ([Responses](#to-single-response)) |
| The list response | {attr}`list_response_schema <fastapi_restly.views.BaseRestView.list_response_schema>` ([Responses](#list-schema)) |
| The response shape of every route | endpoint methods replaced on a base view ([Responses](#every-route-shape)) |
| One option of a route: status code, name, path, dependencies | {func}`fr.modify_route <fastapi_restly.views.modify_route>` ([CRUD Routes](#modify-route)) |
| The headers or the parameters of one route, or what it returns | `<verb>_endpoint` ([below](#replace-endpoint-method)) |
| A path that is not CRUD: a lookup, an action such as publish | a method with {func}`@fr.get <fastapi_restly.views.get>` or {func}`@fr.post <fastapi_restly.views.post>` ([read route](#custom-read-route), [action route](#custom-action-route)) |
| A lookup by another key, such as a slug or an email | In a custom route, call {meth}`handle_get_one <fastapi_restly.views.RestView.handle_get_one>` with an expression ([below](#natural-key-route)) |
| A list of a subset, such as one project's tasks | In a custom route, call {meth}`handle_get_many <fastapi_restly.views.RestView.handle_get_many>` with `where=` ([Scopes](#per-read-where)) |
| One action that changes several rows, such as a new order of the rows | a {meth}`write_action <fastapi_restly.views.RestView.write_action>` block with `obj=None`, in a custom route ([below](#write-several-rows)) |
| One commit over several writes | {meth}`shared_write_action_commit <fastapi_restly.views.RestView.shared_write_action_commit>` in a custom route ([below](#shared-write-action-commit)) |
| The same change on several views | a base view or a mixin ([Views](#share-behaviour-with-base-views)) |

Start at the business method of a write, or at the declarations of a read.
Move a side effect that depends on the commit to a hook. Replace the endpoint
method only when a change to the HTTP contract needs code.

(business-methods)=
## Change what an action does

Override the business method of the action:
{meth}`create <fastapi_restly.views.RestView.create>`,
{meth}`update <fastapi_restly.views.RestView.update>` or
{meth}`delete <fastapi_restly.views.RestView.delete>`. Override only the
one you need. The handler still checks permissions and commits around it.
The reads have no business method; see
[Add a value to each row](#value-on-each-row).

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

    async def create(self, data):
        user = await super().create(data)
        user.password = hash_password(data.password)
        return user
```

{meth}`handle_create <fastapi_restly.views.RestView.handle_create>` still calls
`authorize`, saves and commits. The override only changes the object before
the save. The schema field and the column have the same name, because the
default `create` passes every writable schema field that the client sent to
the model's constructor. So the plain password goes into `password` first, and
the override replaces it with the hash. The save comes after `create`
returns, so the database only gets the hash. `fr.WriteOnly` keeps the field
out of every response.

The generated update schema takes `password` too. Without more code,
`PATCH /users/{id}` stores a new password as plain text. So hash it in
`update` as well:

```python
    async def update(self, obj, data):
        user = await super().update(obj, data)
        if "password" in data.model_fields_set:
            user.password = hash_password(data.password)
        return user
```

The default `update` writes only the fields that the request sends, so the
check uses the same set. The other way is an
[explicit update schema](#explicit-write-schemas) without `password`, with a
separate route that changes the password. The SaaS example does this: its
change-password route checks the current password first
([`app/users/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/users/views.py)).

(no-id-in-create)=
### The new object has no id in `create`

`create` returns the object before the save. So inside `create`, the new
object has no primary key yet. The values that the database or a mapper event
sets at the flush, such as a slug or a tenant id, are not there either. Code
that reads them gets `None`, not an error.

Most code that needs these values runs after the save. An outbox row goes in
[`before_action_commit`](#transaction-hooks), which gets the saved object.
When `create` itself needs the id, flush, as in plain SQLAlchemy:

```python
    async def create(self, data):
        report = await super().create(data)
        await self.session.flush()  # report.id is set now
        report.file_path = f"reports/{report.id}.pdf"
        return report
```

The flush writes the row, and the save writes the path with an `UPDATE`.

:::{admonition} Details
:class: details

- Nothing is written before the save, so a query inside `create` does not
  see the new object. A check such as "is this email taken?" after
  `super().create()` finds only the rows that are already in the database,
  as it should. Restly runs the business method under
  {attr}`session.no_autoflush <sqlalchemy.orm.Session.no_autoflush>` for
  this. Without it, a sync session would write the half-built object before
  the query.
- For the same reason, a query in a business method or a `write_action`
  block does not see any of your unflushed changes. A loop that looks a row
  up and adds it when it is missing, or a count of rows against a limit,
  must call `flush()` before the query.
- A `try` or a `with` around `super().create()` catches no error of the
  INSERT, because the INSERT runs at the save, after `create` returns. To
  catch it in `create`, flush inside the `try`.
- "Nothing is written" is about the objects in the session. A statement that
  you run yourself, such as
  `await self.session.execute(update(Product).values(...))`, goes to the
  database at once. It is part of the same transaction, so the commit
  of the action commits it, and an error rolls it back.
- Often you do not need the id at all. Link the objects with a
  relationship, such as `line.order = order`, and the flush copies the key.
:::

(value-on-each-row)=
### Add a value to each row

A value that needs no query, such as whether the caller may edit the row,
goes in
{meth}`to_single_response <fastapi_restly.views.BaseRestView.to_single_response>`.
Every response of the view passes there: one row, each row of a list, the
row after a write, and a custom route that returns the row:

```python
    def to_single_response(self, obj):
        response = super().to_single_response(obj)
        return response.model_copy(update={"can_edit": obj.owner_id == Current.user_id()})
```

`Current.user_id()` is the id of the caller
([Request Context](context.md)). The view's main schema declares the
field:

```python
    can_edit: fr.ReadOnly[bool | None] = None
```

It is not a column, so `ReadOnly` keeps it out of the create and update
bodies.

A value that needs a query, such as the number of tasks of a project, is SQL
on the model. A `column_property` loads with the row, in the same SELECT. So
a list of 50 projects still runs one query for the page:

```python
from sqlalchemy import func, select
from sqlalchemy.orm import column_property

Project.task_count = column_property(
    select(func.count(Task.id))
    .where(Task.project_id == Project.id)
    .correlate_except(Task)
    .scalar_subquery()
)
```

Declare `task_count: fr.ReadOnly[int] = 0` in the main schema, as for
`can_edit`. The save after a write loads the value again, so the create and
update responses show it too. The list gets filters and a sort key for it,
such as `?sort=-task_count`; see [Filter a list](#filter-a-list). The SaaS
example sets two task counts on its
projects this way
([`app/tasks/models.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/tasks/models.py)),
and `can_edit` in `ProjectView.to_single_response`
([`app/projects/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/projects/views.py)).

Other changes to a list have their own place. A [scope](#view-scope) hides
rows.
{meth}`apply_list_params <fastapi_restly.views.RestView.apply_list_params>`
changes how the list params filter, sort and page
([Filtering, Sorting & Pagination](filtering.md)).
{meth}`get_loader_options <fastapi_restly.views.BaseRestView.get_loader_options>`
loads more for each row ([Relationships](#eager-load-extra-relationships)).

:::{admonition} Details
:class: details

The `column_property` is set after both classes, because the expression names
`Task`. Place it in the module of `Task` when that module imports `Project`.
For type checkers, declare it on the class under `if TYPE_CHECKING:`, as
`task_count: Mapped[int] = mapped_column(init=False)`. Every SELECT of the
model computes a `column_property`, also `session.get()`. The list count does
not: it counts the primary keys.

A value that only one view shows can be a `query_expression()` on the model
instead. The view fills it with `with_expression(...)` in
{meth}`get_loader_options <fastapi_restly.views.BaseRestView.get_loader_options>`.
Every read of the view applies the loader options, and so does the save after
a write. So the value is there in every response of the view, also after
`POST`. Another view of the model, or `session.get()`, leaves it unloaded.

A custom route that names another class in `response_model=` skips
`to_single_response`: FastAPI builds the response from the object, so the
value is missing. Declare the view's response instead, as
[The response of a custom route](#custom-route-response) explains.

For a value from another service, or from a cache, replace the endpoint
method. It calls the handler, gets the values for the whole page in one
call, and builds the response:

```python
from fastapi_restly.views import ListResponseSchema


@fr.get("/")
async def get_many_endpoint(self, list_params) -> ListResponseSchema:
    result = await self.handle_get_many(list_params)
    ratings = await rating_service.for_ids([p.id for p in result.objects])
    for project in result.objects:
        project.rating = ratings.get(project.id)
    return result
```

A read that does not come from the database at all replaces the endpoint
method in the same way, and calls `authorize` itself.
:::

(domain-utilities)=
## Build and save objects

A write business method changes objects in the session and returns the
object. The handler saves what it returns. The defaults use the functions in
{mod}`fr.objects <fastapi_restly.objects>`:

| Business method | Default |
|---|---|
| `create(data)` | {func}`async_make_new_object(self.session, self.model, data) <fastapi_restly.objects.async_make_new_object>`: builds a new ORM object from the schema and adds it to the session. It skips the fields that the schema marks read-only. |
| `update(obj, data)` | {func}`async_update_object(self.session, obj, data) <fastapi_restly.objects.async_update_object>`: writes the writable fields that the client sent onto the object. |
| `delete(obj)` | `await self.session.delete(obj)`: marks the row for deletion. |

None of them flushes or commits. After the business method, the commit
bracket saves the object that it returns. It adds the object to the session
when it is new, flushes, refreshes the object and loads it with the view's
loader options. Then it runs `before_action_commit`, commits and runs
`after_action_commit`.

So an override calls `super()`, and changes the object before or after that
call. To build the object in another way, build it yourself and return it:

```python
    async def create(self, data):
        # this model's constructor takes the values in another form
        return Product.from_catalog_entry(data.catalog_entry)
```

The handler has already checked the references in `data`
(`fr.IDRef` and `fr.MustExist`, see [Relationships](relationships.md)). So an object that you build yourself
keeps those rules, and the bracket adds and saves it.

:::{admonition} Details
:class: details

The functions in {mod}`fr.objects <fastapi_restly.objects>` are for another
model and for code outside a view:
{func}`async_make_new_object <fastapi_restly.objects.async_make_new_object>`,
{func}`async_update_object <fastapi_restly.objects.async_update_object>`,
{func}`async_save_object <fastapi_restly.objects.async_save_object>` and
{func}`async_delete_object <fastapi_restly.objects.async_delete_object>`, and
their sync versions. See
[The same operations outside a view](#advanced-object-helpers). They do not
call `authorize` or the hooks. `async_make_new_object` and
`async_update_object` check the references too. In a CRUD route the handler
has checked them first, and the second check does not query the database
again. `async_save_object` refreshes the object, but it does not load the
relationships that the view's response schema names.

The save writes every change in the session. It refreshes the object that the
business method returns, and every other row of the view's model that the
action wrote. A row of another model that the method changes, such as a line
of an order, is written but not refreshed. When the change set a foreign key
column of such a row, such as `line.product_id`, the save unloads that
relationship. So a response that nests the line loads the new product, not
the old one. A business method that you call yourself, outside a handler or a
`write_action` block, writes nothing: the handler does the save.

Up to 0.11, views had the methods `make_new_object`, `update_object` and
`save_object`. They are removed. A view class that defines one fails when
Python creates the class, with a message that names the replacement.
:::

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

    async def create(self, data):
        user = await super().create(data)
        set_password(user, data.password)
        return user


async def import_user(session, payload) -> User:
    user = await async_make_new_object(session, User, payload)
    set_password(user, payload.password)
    await async_save_object(session, user)
    await session.commit()
    return user
```

The script has no handler, so it saves and commits itself. Do not move logic
out of the view before there is a second caller.

A value that every write path must set does not need this. Put it on the
model ([Models](#write-path-values)), and every path gets it.

(what-commits)=
## What commits, and when

Restly commits for you, once for each write action. The handler of a write
route, or a {meth}`write_action <fastapi_restly.views.RestView.write_action>`
block in a custom route, runs `before_action_commit`, commits, and then runs
`after_action_commit`. Your business methods and hooks do not commit. For code
that must commit, see
[Code that must commit in a write action](#atomic-write-actions).

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

A session generator of your own decides how a session is made and closed. It
does not change who commits, unless it commits itself, which it must not do;
see [Build sessions with your own generator](#own-session-generator).
:::

(write-action-error)=
### When a write action raises

When a write action raises, Restly rolls back what the action changed, and
the session stays usable. So a route can catch the error of one write and go
on:

```python
    from fastapi import HTTPException

    @fr.post("/bulk", status_code=201)
    async def bulk_create(self, items: list[OrderCreate]) -> list[ResponseSchema]:
        orders = []
        for item in items:
            try:
                orders.append(await self.handle_create(item))
            except HTTPException:  # also fr.exc.Conflict, for a duplicate value
                continue
        return orders
```

Each order is committed on its own. On `fr.RestView`, use the sync handlers.
To save all orders in one commit, see
[Commit several writes together](#shared-write-action-commit).

:::{admonition} Details
:class: details

Each outermost write action runs in a savepoint, SQLAlchemy's
{meth}`session.begin_nested() <sqlalchemy.orm.Session.begin_nested>`. The
savepoint begins after `authorize`, the reference check and the snapshot, and
it ends at the commit. When the action raises before the commit, Restly rolls
back to the savepoint. This undoes only what the action changed. Rows that
the action did not change stay loaded, so async code can still read them.
Rows that it changed, such as the row of an update, are expired. In async
code, call `await session.refresh(obj)` before you read such a row. Inside a
`shared_write_action_commit()` block, or inside the block of another action,
an action has no savepoint of its own; see
[Commit several writes together](#shared-write-action-commit). A view with
`atomic_write_actions = False` has no savepoint; see
[Code that must commit in a write action](#atomic-write-actions).

A savepoint begins with a flush of what is pending in the session, as
`begin_nested()` always does. So an object that a route adds to the session
before the write action is written when the action begins, and an error in
the action does not undo it. This is also true for a new object that a route
appends to a collection of a loaded row. Give a new object to `write_action`
as `obj`, without `session.add()`, and connect it to loaded rows inside the
block: the action then adds it inside its savepoint.

An error from `after_action_commit` does not undo the order: the order is
saved. See [When `after_action_commit` raises](#after-action-commit-raises).
When the commit itself fails, for example on a deferred constraint, Restly
rolls back the whole transaction. This also expires the rows that were loaded
before. On SQLite with the default settings of the driver, the end of the
savepoint can already commit, just before the commit; see
[Transactions on SQLite](#sqlite-transactions).

The savepoint costs two more statements for each write: `SAVEPOINT` and
`RELEASE SAVEPOINT`.

SQLAlchemy calls the listeners of the session for a savepoint too:
`after_begin` when it begins, `before_commit` and `after_commit` when it ends,
and `after_rollback` when it rolls back. So `before_commit` and `after_commit`
run twice for each outermost write action: when the savepoint ends, and at the
commit. The first time is before the commit, which can still fail. A listener
that must run only at the commit returns early when
`session.in_nested_transaction()` is true. Work that must wait for the commit,
such as an email, belongs in `after_action_commit`. On SQLite, the savepoint
can also make `database is locked` come sooner; see
[Turn on WAL for SQLite](#sqlite-wal).
:::

(atomic-write-actions)=
### Code that must commit in a write action

A write action is one transaction, so Restly refuses a `session.commit()` in it.
The client gets a 500, and nothing is saved. Remove that commit if you can. The
action then saves all of its work or none of it.

Some code must keep its commit: a library that commits, or a function that a job
or a script also calls. For that code, set
{attr}`atomic_write_actions <fastapi_restly.views.BaseRestView.atomic_write_actions>`
to `False`, on the view or on a base view:

```python
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserSchema
    atomic_write_actions = False

    async def create(self, data):
        return await register_user(self.session, data)  # this function commits
```

This setting takes away two things: the refusal of a commit, and the savepoint.
All else stays as on other views: `authorize`, the save, both hooks, a commit at
the end, and a 409 for a conflict.

:::{warning}
The action is no longer one transaction. What the code committed stays saved when
a later step raises: the client gets an error, the rows are saved, and
`after_action_commit` does not run. This is also true for an error in
`before_action_commit`. What that hook writes is not saved together with the rows
of that commit, so an outbox row can be missing.
:::

An error rolls back all that is not committed, and not only the changes of the
action, because there is no savepoint. A route that catches the error and goes on
must load its rows again, because the rollback expires them.

:::{admonition} Details
:class: details

The outermost write action decides, and the actions inside it join it, as on
other views. So a handler inside the `write_action` block of this view does not
commit on its own. The commit comes at the end of the outermost action, and then
the `after_action_commit` hooks of all its actions run. Code inside an action of
this view may commit, also in a handler of a view that keeps the default. Inside
an action of a view that keeps the default, a commit is refused, also in a
handler of this view. An outermost
[`shared_write_action_commit()`](#shared-write-action-commit) block always refuses
a commit, also on this view. Inside an action of this view, such a block joins
the action, as an action does.

Restly refuses only `session.commit()`. A commit on the connection, such as
`session.connection().commit()`, is not refused, on any view.

On this view, Restly does not check what else the code does with the transaction:

- `session.rollback()` and `session.close()` undo all that is not committed, but
  the action goes on, and the client gets a 2xx. So changes can be lost without
  an error. A method that rolls back or closes the session must raise.
- `with session.begin():` raises when a transaction is open, and the load of an
  update opens one.

The save leaves alone a row that your code took out of this session, because it
expunged the row or gave it to another session. The save does not add that row
and does not read it back, so a task can still use it. The hooks get it as `new`,
but a change that a hook makes to it is not saved. The response uses it as it is,
so what the response reads must be loaded. A row with a change is different: the
save adds it and writes the change. A new row is different too: the save adds
it. After a delete and a commit in your code, the hooks get `new=None`.

[Django](https://docs.djangoproject.com/en/5.2/topics/db/transactions/) has the
same choice. Its `atomic` refuses a commit, and `non_atomic_requests` takes one
view function out of the transaction of the request. Here the setting is for
every route of the view.
:::

(transaction-hooks)=
## Run code before or after the commit

Every write action calls two hooks around its commit:

- {meth}`before_action_commit(action, new, old=None) <fastapi_restly.views.RestView.before_action_commit>`
  runs inside the transaction, before the commit. What it writes is committed
  together with the change, or not at all. Use it for outbox rows and audit
  rows. A view with `atomic_write_actions = False` is different; see
  [Code that must commit in a write action](#atomic-write-actions).
- {meth}`after_action_commit(action, new, old=None) <fastapi_restly.views.RestView.after_action_commit>`
  runs after the commit, when the change is saved. Use it for email, webhooks
  and cache invalidation.

The hooks run for the CRUD writes and for every `write_action` block. Reads do
not call them, and neither do the `fr.objects` functions. The arguments are:

- `action`: the name of the action, such as `"create"`, or the name that a
  custom route gives `write_action`. See [Action names](#action-names).
- `new`: the row that the action is about, after the save. It is `None` when
  the action removed the row, as a delete does, or when the action has no
  single row. On a session that expires on commit, `snapshot(new)` in
  `after_action_commit` is empty, and Restly warns; see
  [Session factory defaults](#session-factory-defaults).
- `old`: a dict of that row's column values before the action, from
  {meth}`snapshot <fastapi_restly.views.BaseRestView.snapshot>`. It holds only
  the columns that were loaded. It is `None` when the row is new, as on a
  create, or when the action has no single row.

So `old` and `new` tell what happened: a new row has no `old`, a removed row
has no `new`, and a changed row has both. This is the same for the CRUD
handlers and for every `write_action` block. So a hook can check what changed
without a list of routes:

```python
    async def after_action_commit(self, action, new, old=None):
        if old and new and old["status"] != new.status:  # PATCH, archive, ...
            await notify_status_change(new.id, new.status)
```

Do not change `new` or the database in `after_action_commit`. The commit is
done, so the change is not saved. A change to `new` still shows in the
response of this request, which hides the problem. Make the change in the
business method or in `before_action_commit`.

:::{admonition} Details
:class: details

After a delete, `new` is `None` and `old` holds the columns of the removed
row. A [soft delete](#soft-delete-recipe) keeps the row, so `new` is the row
with its new timestamp. A delete that must finish outside the request can
mark the row in `delete` in the same way. After the commit,
`after_action_commit` starts the real delete:

```python
    async def delete(self, obj):
        obj.status = "pending_deletion"  # no super(): the row stays

    async def after_action_commit(self, action, new, old=None):
        if action == self.Action.DELETE:
            await enqueue_async_delete(new.id)  # a worker deletes the row
```

`old` comes from the action, not from the database. A create always gives
`old=None`, also when your `create` returns a row that exists already, such
as a tag with the same name. A hook that reads `old is None` as "a new row"
is wrong on that path. To give the hooks the stored values, look the row up
in a custom route, and change it in a `write_action` block with the row as
`obj`.

Code that a route runs after its `write_action` block also runs after the
commit, but only when the block commits itself. Inside
[`shared_write_action_commit()`](#shared-write-action-commit), or inside the
block of another action, that code runs before the commit. A hook runs after
the commit in both cases. So work that reads the committed row, such as
starting a job, goes in `after_action_commit`.

Restly finds `new` from the state of the row after the flush, as SQLAlchemy
reports it. A row that the action deleted with `session.delete()` or with an
ORM statement such as `delete(Post)` is gone. A Core `delete()` on the table
does not tell the session, so the refresh of the row fails with SQLAlchemy's
`InvalidRequestError`. Delete the row that `obj` names through the ORM.
:::

(after-commit-background-task)=
### Run the work after the response

Work in `after_action_commit`, such as sending an email, makes the client
wait for it. To answer first, give the work to FastAPI's
{class}`BackgroundTasks <fastapi.BackgroundTasks>`. Declare it as a
[class attribute](#dependency-injection-on-class-attributes) of the view, and
add the task in the hook:

```python
from fastapi import BackgroundTasks


class OrderView(fr.AsyncRestView):
    ...
    background_tasks: BackgroundTasks

    async def after_action_commit(self, action, new, old=None):
        if action == self.Action.CREATE:
            self.background_tasks.add_task(send_confirmation_email, new.id)
```

FastAPI runs the task after it sends the response. The order is committed
then, so a task that opens its own session finds it. A task queue works the
same way: enqueue the job in `after_action_commit`, with the id of the row.
Do not enqueue it in `before_action_commit`: a worker can start the job
before the commit, when other sessions cannot see the row yet, and the commit
can still fail. A task that fails does not undo the write; see the next
section.

(after-action-commit-raises)=
### When `after_action_commit` raises

The write is already committed, so an exception from the hook cannot undo it.
The request still fails, and a client that tries again repeats the write. For
an effect that may fail without harm, catch and log the error in the hook:

```python
    async def after_action_commit(self, action, new, old=None):
        if action == self.Action.CREATE:
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
## Replace the endpoint method of one route

Business methods and hooks keep the HTTP contract of the CRUD route. To change
only an option of the route, such as its status code, use
{func}`fr.modify_route <fastapi_restly.views.modify_route>`; see
[Change one option of a route](#modify-route). Replace the endpoint method
when the change needs code: the headers, the parameters or what the route
returns. To change the shape of the response body, such
as an envelope around one object, see
[Change the shape of one route](#one-route-shape) on Responses. It replaces
the endpoint method in the same way.

Define a method with the same name, such as `delete_endpoint`, and give it a
route decorator. Most replacements call the handler and only change the
response:

```python
from fastapi_restly.views import ResponseSchema


@fr.include_view(app)
class ProductView(fr.AsyncRestView):
    prefix = "/products"
    model = Product
    schema = ProductSchema

    @fr.delete("/{id}", status_code=200)
    async def delete_endpoint(self, id: int) -> ResponseSchema:
        obj = await self.get_one(id)               # load through the scope, or 404
        response = self.to_single_response(obj)    # the row as GET /{id} sends it
        await self.handle_delete(id)               # authorize, delete, commit
        return response
```

The default `DELETE /{id}` answers `204 No Content`. This version answers
`200` with the deleted row, as React Admin's `ra-data-simple-rest` expects
([React Admin](react_admin.md)). It builds the response before the row is
gone. `ResponseSchema` makes the response schema the `response_model`, so only
its fields go out, as from `GET /{id}`, and OpenAPI shows them.

:::{admonition} Details
:class: details

When Restly registers the view, an endpoint method that the class defines
replaces the inherited method with the same name. The other inherited
endpoint methods stay. A method with no route decorator is not a route, so the
view then has no such route. With `fr.configure(warn_on_misuse=True)`, Restly
warns about it.

The built-in endpoint methods get the view's types from markers, such as
`get_one_endpoint(self, id: IDType) -> ResponseSchema`. A replacement gets the
view's types only where it uses the markers too; see
[Name the view's schemas in a custom route](#name-the-view-schemas). A
parameter or a return without an annotation gets no type. Restly keeps an
annotation that you write, so `data: ProductReplace` validates the
request body against `ProductReplace`. `list_params` is the exception: it
always gets the view's
{attr}`list_params_schema <fastapi_restly.views.BaseRestView.list_params_schema>`.
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

from fastapi_restly.views import ResponseSchema


@fr.include_view(app)
class ProductView(fr.AsyncRestView):
    prefix = "/products"
    model = Product
    schema = ProductSchema

    @fr.get("/", response_model=list[ResponseSchema])
    async def get_many_endpoint(self, list_params):
        result = await self.handle_get_many(list_params)
        serialized = [
            self.to_single_response(obj).model_dump(mode="json", by_alias=True)
            for obj in result.objects
        ]
        return fastapi.Response(
            content=json.dumps(serialized),
            media_type="application/json",
            headers={"X-Total-Count": str(result.total_count)},
        )
```

The route sends a plain list, not the default envelope, so it names its
`response_model`: a list of the view's response schema. Without it, OpenAPI
shows no schema for the response. The route builds the body itself, so it
dumps each row `by_alias=True`, as FastAPI does for a response model.
[Name the view's schemas in a custom route](#name-the-view-schemas)
explains the markers, such as `ResponseSchema`.

### Share a changed route across views

When several views need the same contract, put the replacement in a mixin.
List the mixin before the view class, so Python finds its method first:

```python
from fastapi_restly.views import IDType, ResponseSchema


class DeleteReturnsObjectMixin:
    @fr.delete("/{id}", status_code=200)
    async def delete_endpoint(self, id: IDType) -> ResponseSchema:
        obj = await self.get_one(id)
        response = self.to_single_response(obj)
        await self.handle_delete(id)
        return response


@fr.include_view(app)
class ProductView(DeleteReturnsObjectMixin, fr.AsyncRestView):
    prefix = "/products"
    model = Product
    schema = ProductSchema
```

`IDType` and `ResponseSchema` give each view that uses the mixin its own id
type and response schema.

The [React Admin](react_admin.md) views use this pattern. They replace
{meth}`get_many_endpoint <fastapi_restly.views.RestView.get_many_endpoint>`
for the `ra-data-simple-rest` contract, and keep the handlers, the loads and
the business methods.

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

A read of your own starts from
{meth}`self.select() <fastapi_restly.views.BaseRestView.select>`: the view's
model with its scope and its loader options. So the rows are the ones that
the view shows, and each row loads what the response needs:

```python
    @fr.get("/recent")
    async def recent(self) -> list[ResponseSchema]:
        await self.authorize(fr.Action.GET_MANY)
        stmt = self.select().order_by(Post.created_at.desc()).limit(5)
        return list(await self.session.scalars(stmt))
```

`ResponseSchema` from `fastapi_restly.views` stands for the view's response
schema. The route declares a list of it, so Restly passes each row through
`to_single_response`, as the list route does; see
[The response of a custom route](#custom-route-response). The route builds
its own read, so it calls `authorize` itself.

The SaaS example's `GET /users/me` returns the caller's own row:

```{literalinclude} ../example-projects/saas/app/users/views.py
:language: python
:pyobject: UserView.get_current_user
```

`Current.user_id()` is the id of the caller
([Request Context](context.md)).

:::{admonition} Details
:class: details

`ResponseSchema` stands for the view's response schema, `UserResponse`, so
OpenAPI shows the same type as for `GET /users/{id}`, and the row goes
through the view's response step
([The response of a custom route](#custom-route-response)). The
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
[scope](#view-scope), the loader options, and the 404.

```python
    @fr.get("/by-email/{email}")
    async def get_by_email(self, email: str) -> ResponseSchema:
        return await self.handle_get_one(User.email == email)
```

When every item route takes the other key, override
{meth}`where_id <fastapi_restly.views.BaseRestView.where_id>` instead. It
gets the id from the path and returns the WHERE that picks the row. Set
{attr}`id_type <fastapi_restly.views.BaseRestView.id_type>` to the type of
the value in the path:

```python
from sqlalchemy import ColumnElement


@fr.include_view(app)
class RecipeView(fr.AsyncRestView):
    prefix = "/recipes"
    model = Recipe
    schema = RecipeSchema
    id_type = str

    def where_id(self, id: str) -> ColumnElement[bool]:
        return Recipe.slug == id
```

Now `GET`, `PATCH` and `DELETE /recipes/{slug}` find the recipe by its slug,
and so does `self.get_one(slug)` in your own routes. To change the error for
a row that does not exist, override
{meth}`not_found <fastapi_restly.views.BaseRestView.not_found>`. It returns
the exception that `get_one` raises:

```python
    def not_found(self, id: str | None) -> Exception:
        return fr.exc.NotFound(f"No recipe has the slug {id!r}")
```

:::{admonition} Details
:class: details

`where_id` is a plain `def`, also on an async view: it builds SQL and runs
no query. `not_found` may be `async def` on an async view, and Restly awaits
it. When a route loads by an expression, `not_found` gets `None`, because the
404 body is no place for SQL. A hook that returns the wrong kind of value
raises `TypeError`.

Combine columns with `sqlalchemy.and_(...)`. This is how a route reaches a
row of a model with a composite primary key. A plain id cannot name such a
row, so a view of such a model must override `where_id` to read the parts
from the path, such as `"3-7"`. Set `id_type = str` too: without it, the
path takes only an integer, and `/seats/3-7` is a 404 before `where_id`
runs. Then the default item routes work for the model:

```python
class SeatView(fr.AsyncRestView):
    prefix = "/seats"
    model = Seat
    id_type = str  # the id in the path is "3-7"

    def where_id(self, id: str) -> ColumnElement[bool]:
        row, _, number = id.partition("-")
        if not (row.isdigit() and number.isdigit()):
            raise self.not_found(id)
        return sqlalchemy.and_(Seat.row == int(row), Seat.number == int(number))
```

A view that keeps the default `where_id` fails when it registers, unless it
leaves the item routes out: `get_one_endpoint = None`, `update_endpoint = None`
and `delete_endpoint = None`.

The expression narrows the read inside the scope. It never reaches past it,
so a row that the view hides stays a 404 under any key. Only
{attr}`scope <fastapi_restly.views.BaseRestView.scope>` changes what the view
sees. When more than one row matches, Restly raises SQLAlchemy's
`MultipleResultsFound` and does not return the first row: a key that matches
several rows is a bug in the expression.

The expression, and the one that `where_id` returns, compares columns of the
view's model. A column of another table raises `TypeError`, because SQL would join every row of that table and
could pick a wrong row. For a condition on a related row, use `.has()` or
`.any()`, such as `Book.shelf.has(Shelf.code == code)`.

{meth}`handle_update <fastapi_restly.views.RestView.handle_update>` and
{meth}`handle_delete <fastapi_restly.views.RestView.handle_delete>` take the
same expression. So a custom `PATCH /users/by-email/{email}` route that calls
`handle_update(User.email == email, data)` runs the same steps as
`PATCH /users/{id}`. The SaaS example reads and updates projects by slug in
[`app/projects/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/projects/views.py),
next to the routes by id. That is why it passes an expression, and does not
override `where_id`.
:::

(custom-action-route)=
## Add a custom action route

An action changes state: publish, archive, recalculate. Declare it with
{func}`@fr.post <fastapi_restly.views.post>`, or with
{func}`@fr.patch <fastapi_restly.views.patch>` or
{func}`@fr.delete <fastapi_restly.views.delete>`. Most actions take one of
four forms.

**Change one row in a `write_action` block.** Load the row with
{meth}`get_one <fastapi_restly.views.RestView.get_one>`. Then change it inside
{meth}`write_action <fastapi_restly.views.RestView.write_action>`, under the
name of the action. `obj` names the row that the action is about. The blog
example publishes a post:

```{literalinclude} ../example-projects/blog/blog/main.py
:language: python
:start-at: class Action(fr.Action):
:end-at: return post
```

The view's `Action` class lists its actions: the five CRUD actions of
{class}`fr.Action <fastapi_restly.views.Action>`, and `PUBLISH`. See
[Action names](#action-names).

When the block starts, Restly calls `authorize("publish", obj=post)` and takes
the snapshot of the row. With `data=`, it also checks the references in the
data, as a CRUD write does. When the block ends, Restly saves, as after a
business method: it flushes, refreshes `post` and loads it with the view's
loader options. Then it runs `before_action_commit`, commits, and runs
`after_action_commit`, each with the action name `"publish"`. So the block
only changes the row. If the block raises, as it does here for a post that is
already published, nothing is committed. The route loads with `get_one`, not
with `handle_get_one`, because `write_action` authorizes the action itself.
[Authorize a custom action](#authorize-custom-actions) shows how `authorize`
handles a new name.

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
    @fr.patch("/me")
    async def update_current_user(self, request: UpdateMeRequest) -> ResponseSchema:
        return await self.handle_update(Current.user_id(), request)
```

`UpdateMeRequest` has only `name` and `email`, so this route cannot change
other fields. The default `update` writes the fields of the schema it
receives, and only the fields that the client sent. A change to the `update`
business method applies here too.

**Create in a block of your own.** Some creates need more than `create` does,
such as a parent row and its child rows. Build the new object, and pass it to
`write_action` as `obj`:

```python
    @fr.post("/with-lines", status_code=201)
    async def create_with_lines(self, data: OrderWithLines) -> ResponseSchema:
        order = Order(customer_id=data.customer_id)
        async with self.write_action(self.Action.CREATE, obj=order, data=data):
            order.lines = [OrderLine(sku=line.sku) for line in data.lines]
        return order
```

The order is not in the database yet, so `authorize` gets `obj=None`, as on
`POST /`, and `old` is `None`. Pass the request as `data=`: then
`authorize` gets it, so a create rule that reads the payload works here too.
Restly checks the references in `data` before the block, as the create
handler does. Then it adds the order to the session. When the block ends, it
saves the order with the lines that its relationship holds, and the hooks
receive the order as `new`.

The route builds the order before the block, so before `authorize` and the
reference check. That is safe, because building an object writes nothing. But
set a reference to a loaded row by its key, as `customer_id=` above, or set it
inside the block. With `Order(customer=customer)` before the block, the loaded
customer holds the new order in its `orders`. The action begins with a flush,
and SQLAlchemy then warns that the order is not in the session. A check of the
built object, such as a rule about its lines, goes inside the block, where
`authorize` has already passed.

(write-several-rows)=
**Change several rows in one action.** Pass `obj=None`: the action has no
single row. When the block ends, Restly saves every row of the view's model
that the block changed. It reloads them together, with the view's loader
options, so each row comes back as the database has it:

```python
    class Action(fr.Action):
        REORDER = "reorder"

    @fr.put("/positions")
    async def reorder(self, items: list[CookbookPosition]) -> list[ResponseSchema]:
        cookbooks = []
        async with self.write_action(self.Action.REORDER, obj=None, data=items):
            for item in items:
                cookbook = await self.get_one(item.id)
                cookbooks.append(await self.update(cookbook, item))
        return cookbooks
```

`authorize` gets `obj=None`, once, and the hooks get `new=None` and
`old=None`. The block calls the business method `update`, so a change to it
applies here too. Restly checks the references in each schema of the list.
The reload runs one query for each 500 rows, also for rows that the route
does not return.

:::{admonition} Details
:class: details

A block that needs the id of the new row before it ends, for rows that refer
to it by a plain id, calls `await self.session.flush()`. Restly has added the
new object to the session, so the flush inserts it. The SaaS example's
`POST /uploads` does this for an upload and its lines, and
`POST /projects/{id}/clone` does it for a project and its copied tasks
([`app/uploads/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/uploads/views.py),
[`app/projects/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/projects/views.py)).
Rows of another model, such as the lines, are written but not refreshed.

When each row of several needs its own `authorize` call and its own hooks,
call the handler for each row inside
[`shared_write_action_commit()`](#shared-write-action-commit) instead. Or call
`authorize("update", obj=cookbook, data=item)` in the loop yourself.

A block does not take a list as `obj`. Inside the block, nothing is written
until it ends, so a query there does not see your changes. When the query
must see them, call `session.flush()` first.

After the block, `obj` has the relationships of the view's loader options,
and the ones that the model loads eagerly, such as with `lazy="selectin"`.
The save unloads the rest, so a changed foreign key never shows the old
related row. This holds also for a relationship that the
route loaded before the block. On an async view, read such a relationship
after the block with `await obj.awaitable_attrs.<name>`, or add it to
{meth}`get_loader_options <fastapi_restly.views.BaseRestView.get_loader_options>`.

Restly checks the references in `data`, also in the schemas nested in it and
in each item of a list; see [Check that the referenced row exists](#reference-exists).

The routes above declare `ResponseSchema`, or a list of it. So Restly sends
the rows that they return through the view's response step, and OpenAPI
shows the same types as for the CRUD routes; see
[The response of a custom route](#custom-route-response). A route
that builds a schema with `model_construct()` skips validation, so its
reference fields need the right form: see
[Build references in a custom route](#idref-custom-routes).
:::

(shared-write-action-commit)=
## Commit several writes together

A handler or a `write_action` block commits on its own, unless it runs
inside another `write_action` block, which then owns the commit. To commit
several of them at once, put them inside a
{meth}`shared_write_action_commit() <fastapi_restly.views.AsyncRestView.shared_write_action_commit>`
block. The outermost block commits once. Then the queued `after_action_commit`
hooks run:

```python
    @fr.post("/bulk", status_code=201)
    async def bulk_create(self, items: list[OrderCreate]) -> list[ResponseSchema]:
        async with self.shared_write_action_commit():
            orders = [await self.handle_create(item) for item in items]
        return orders
```

Inside the block, each action still calls `authorize`, takes the snapshot,
makes its change and runs `before_action_commit`. Then it flushes and returns.
Its changes are not committed yet, and its `after_action_commit` has not run.
So code after a handler call inside the block runs before the commit. Code
that needs the commit, or an after-hook that has run, goes after the block,
or in `after_action_commit`. The route above returns the orders after the
block, so Restly builds their responses after the commit. On `fr.RestView`, use
`with` and the sync handlers. The method has the same name.

:::{admonition} Details
:class: details

The block can also hold the handlers of other views, for an operation on more
than one model. Each create runs the steps of its own view: `authorize`, the
reference checks, the save and the hooks. The SaaS example creates a label and
attaches it to a task:

```{literalinclude} ../example-projects/saas/app/labels/views.py
:language: python
:pyobject: TaskLabelView.create_and_attach
```

The label's handler saves the label, so the link gets its id. The label, the
link and the rows that their `before_action_commit` hooks write are committed
together. If any part fails, none of them is committed. Code that only
belongs to one action, such as copied child rows, goes in that action's
`write_action` block instead, as the clone route does.

Blocks inside each other share the commit when their views share a session.
A `write_action` block counts as such a block, so a handler that you call
inside it joins its commit. The handler saves, so it also writes the changes
of the outer block so far: set the required values of the block's objects
before you call it.
An exception that leaves a `shared_write_action_commit()` block cancels the
shared commit and drops the queued hooks. This also happens when an outer
block catches the exception. For a write action that raises inside the
block, see the savepoint below.
The outermost block runs in a savepoint of its own. When an exception leaves
it, Restly rolls back to that savepoint: what the block changed is undone, and
the session can write again. A direct `session.commit()` inside the block
raises `RuntimeError`, because the outermost block owns the commit.

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
after-hook is dropped. Without the savepoint, a row that failed after its
change began, such as a `create` that refuses after `super().create()`, would
leave a half-done change in the session. So when the route catches such an
error without a savepoint, the commit raises `RuntimeError`, and nothing is
committed. An error before the change, from `authorize` or the reference
check, changed nothing, and the commit goes on. Inside the shared block, the row's error stays a raw
`IntegrityError`, so the route can catch it. An `IntegrityError` that escapes
the shared block, or that its commit raises, becomes a `409`; see
[Unique values and other database conflicts: 409](#default-exception-handling). After the shared commit, the after-hooks of the saved
rows run in order. They stop at the first exception.

The SaaS example runs both forms. `POST /tasks/bulk`, `/tasks/bulk-delete`
and `/tasks/import-csv` put a savepoint around each row inside one
`shared_write_action_commit()` block
([`app/tasks/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/tasks/views.py)).
`POST /task-labels/create-and-attach` commits a label and its link together
([`app/labels/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/labels/views.py)).
:::

## What is available on `self`

Inside every method and custom route, you can use these attributes:

| Attribute | Type | Description |
|---|---|---|
| {attr}`self.session <fastapi_restly.views.RestView.session>` | `AsyncSession`, or `Session` on `RestView` | The database session of the request |
| {attr}`self.request <fastapi_restly.views.BaseRestView.request>` | `fastapi.Request` | The HTTP request |
| {attr}`self.model <fastapi_restly.views.BaseRestView.model>` | the model class | The SQLAlchemy model of the view |
| {attr}`self.schema <fastapi_restly.views.BaseRestView.schema>` | `type[pydantic.BaseModel]` | The main schema |

A class attribute that you declare with `Annotated[..., Depends(...)]`, such
as the current user, is also set on `self` for each request. See
[Declare dependencies as class attributes](#dependency-injection-on-class-attributes).

## Internals

The CRUD handlers and `write_action` share one private implementation of the
commit bracket. It calls `authorize`, checks the references in `data`, takes
the snapshot, and runs the change under `session.no_autoflush`. Then it
saves: it flushes and refreshes the subject. Then one reload with the view's
loader options covers the subject and the other written rows of the view's
model. It does not apply the scope, so a row that the action moved out of it,
such as a soft-deleted row, still comes back. Then it runs
`before_action_commit`, the commit and `after_action_commit`. The outermost
bracket on a session owns the commit. So inside a `write_action` or a
`shared_write_action_commit()` block, a bracket flushes instead of
committing and queues its after-hook for the outermost block. A handler
hands the object that `create` or `update` returns to the bracket as the
subject. `write_action` gets its subject from `obj`. The outermost bracket
opens a savepoint after the snapshot, and an exception rolls back to it; see
[When a write action raises](#write-action-error).

## See also

- [Authorization](authorization.md): who may perform each action.
- [Scopes](scopes.md): which rows a view can see.
- [Views](views.md): registration, dependencies and base views.
- [Models](#write-path-values): values that every write path must set.
- [View Reference](#view-method-surface): every view method, with its
  signature.
