# CRUD Routes

{class}`AsyncRestView <fastapi_restly.views.AsyncRestView>` and
{class}`RestView <fastapi_restly.views.RestView>` give one SQLAlchemy model
five CRUD routes. A subclass sets the URL prefix and the model, and it can
also set the schemas. {func}`include_view()
<fastapi_restly.views.include_view>` registers the routes:

```python
@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
```

`UserView` now serves requests to list, create, get, update and delete users
under `/users`. [Build a Blog API](tutorial.md) shows the app, the database
configuration and the model around such a view, in one file that runs.
`include_view` also works as a direct call; see
[Register a view](#register-a-view).

Each route runs a fixed list of steps, such as loading the row, `authorize`
and the commit. [How a CRUD route runs](#view-request-steps) explains these
steps. Each route also has a page of its own, such as [Update](crud_update.md).
It shows the steps of that route in order, and a table of what you can change.

(choose-async-or-sync)=
## Choose async or sync

Use {class}`AsyncRestView <fastapi_restly.views.AsyncRestView>` for a new
application, unless the application already uses synchronous SQLAlchemy. It
gets an `AsyncSession` through
{data}`fr.AsyncSessionDep <fastapi_restly.db.AsyncSessionDep>`, and the
methods you override are `async def` methods.

Use {class}`RestView <fastapi_restly.views.RestView>` with synchronous
SQLAlchemy sessions, or with libraries that only work synchronously. It gets
a `Session` through {data}`fr.SessionDep <fastapi_restly.db.SessionDep>`,
and the methods you override are plain `def` methods.

Apart from this, the two classes are the same: the same routes, the same
configuration and the same methods to override. You choose for each view, so
one application can use both. [Database & Configuration](database.md) shows
how to configure the sessions. To give one view another session, see
[Use another database on one view](#session-dependency-one-view).
The examples on this page use `AsyncRestView`.

(default-crud-routes)=
(default-crud-behavior)=
## The default routes

For `prefix = "/users"`, `include_view` registers these routes:

| Request | Endpoint method | Input | Response | Status |
|---|---|---|---|---|
| `GET /users` | {meth}`get_many_endpoint <fastapi_restly.views.AsyncRestView.get_many_endpoint>` | Filter, sort and page parameters | A page of objects in a `data` envelope | `200` |
| `POST /users` | {meth}`create_endpoint <fastapi_restly.views.AsyncRestView.create_endpoint>` | `create_schema` | `response_schema` | `201` |
| `GET /users/{id}` | {meth}`get_one_endpoint <fastapi_restly.views.AsyncRestView.get_one_endpoint>` | The id in the path | `response_schema` | `200` |
| [`PATCH /users/{id}`](crud_update.md) | {meth}`update_endpoint <fastapi_restly.views.AsyncRestView.update_endpoint>` | `update_schema` | `response_schema` | `200` |
| `DELETE /users/{id}` | {meth}`delete_endpoint <fastapi_restly.views.AsyncRestView.delete_endpoint>` | The id in the path | Empty body | `204` |

The main path of the list is `/users`, without a slash at the end. Restly
also accepts `/users/`, as a route that OpenAPI does not show.

`GET /users` takes the filter, sort and page parameters that
[Filtering, Sorting & Pagination](filtering.md) defines, and it is paginated by
default.
[Responses](responses.md) shows the response bodies and how to
change the `data` envelope.

A row that does not exist returns `404`, and so does a row that the view's
[scope](scopes.md) hides. Each write commits before Restly builds the
response, and a read does not commit; see
[What commits, and when](#what-commits).

:::{important}
Restly does not authenticate requests, and by default it allows every action
on every route. Add authentication with FastAPI dependencies; see
[Run a dependency on every route](#run-a-dependency-on-every-route). Then
decide per action who may do what in
{meth}`authorize() <fastapi_restly.views.AsyncRestView.authorize>`; see
[Authorization](#authorize-gate).
:::

(item-route-matching)=
### Item paths and static routes

The paths of the built-in `GET`, `PATCH` and `DELETE` routes for one row
follow {attr}`id_type <fastapi_restly.views.BaseRestView.id_type>`. By
default, Restly takes it from the model's primary key. The `PUT` route of a
React Admin view uses the same path.

| ID type | Registered item path | Matching values |
|---|---|---|
| `int` | `/{id:int}` | Digits only, including `0`; no minus sign |
| `uuid.UUID` | `/{id:uuid}` | UUIDs that Starlette's converter accepts |
| Any other type | `/{id}` | Any single path segment, then FastAPI validation |

With an integer or UUID item path, a static route such as `/users/me` still
works when you register it after the view.

An untyped item path, such as the path for string ids, also matches
`/users/me`. Register a static route like that before the item route.

:::{admonition} Details
:class: details

A value that does not match the converter returns `404`, unless another
route matches it. So a negative integer id does not match, even when the row
exists. OpenAPI still shows the path as `/users/{id}`, with an integer or UUID
parameter.

A route that you declare yourself keeps the path you give it. Restly adds
the converter only to a path parameter annotated
{data}`IDType <fastapi_restly.views.IDType>`; see
[Name the view's schemas in a custom route](#name-the-view-schemas). To
accept negative integer ids, replace each item endpoint you need with an
untyped path such as `@fr.get("/{id}")`, and annotate its `id` parameter as
`int`; see
[Replace the endpoint method of one route](#replace-endpoint-method).

The item routes use the name `id` in the path. An existing API can use another
name, such as `{user_id}`. The URLs are the same, but OpenAPI and the clients that
are generated from it show the name. To keep that name, replace each item endpoint,
and `put` on a React Admin view, with a path such as `@fr.get("/{user_id:int}")` and
a parameter `user_id: int`. With the converter, a wrong value still answers `404`,
and `/users/me` still works after the view.

OpenAPI does not allow two paths that differ only in the names of their
parameters. So `/{id}` next to `/{user_id}` on one view raises an error when you
register the view. Use the same name in both paths.
:::

(view-request-steps)=
## How a CRUD route runs

Take `POST /`, the create route. FastAPI calls
{meth}`create_endpoint <fastapi_restly.views.RestView.create_endpoint>`, the
**endpoint method**. It calls
{meth}`handle_create <fastapi_restly.views.RestView.handle_create>`, the
**handler**. The handler runs the steps in this order:

```{code-block} text
:emphasize-lines: 2,4,6,7,9,11,12

POST /
  └─ create_endpoint(data)                # endpoint method: the HTTP contract
       ├─ handle_create(data)             # handler: runs the steps (final)
       │    ├─ authorize("create", data=data)
       │    ├─ check the references in data   # an id that does not exist: 404
       │    ├─ create(data)               # business method: builds the object;
       │    │                             #   nothing is written yet
       │    ├─ save: flush, refresh obj, load it with the loader options
       │    ├─ before_action_commit("create", new=obj, old=None)
       │    ├─ commit
       │    └─ after_action_commit("create", new=obj, old=None)
       └─ to_single_response(obj)
```

The highlighted lines are the methods that you can override in your view.
The page of each route, such as [Update](crud_update.md), shows the steps of
that route.

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

The save is the step that `session.flush()` and `session.refresh()` do in
plain SQLAlchemy. It flushes the session. Then it refreshes the subject, and
every other row of the view's model that the action wrote. It loads them
with the view's
[loader options](#eager-load-extra-relationships), as a read does: the
relationships that the response shows, and any value that you add there. So
the hooks and the response see the saved rows. Rows of other models are
written, but not refreshed.

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

(crud-model-and-schemas)=
## Set the model and the schemas

A CRUD view sets two class attributes:

- {attr}`prefix <fastapi_restly.views.View.prefix>`: the URL prefix of every
  route of the view.
- {attr}`model <fastapi_restly.views.BaseRestView.model>`: the SQLAlchemy
  mapped class that the routes read and write.

Normal models built on SQLAlchemy's `DeclarativeBase` work. Restly's model
defaults, {class}`fr.ModelDefaults <fastapi_restly.models.ModelDefaults>`, are
optional; see [Models](models.md).

If the view does not declare {attr}`schema
<fastapi_restly.views.BaseRestView.schema>`, Restly generates the view's main
schema from `model` when you register the view. From the main schema, it
derives the create, update and response schemas:
{attr}`create_schema <fastapi_restly.views.BaseRestView.create_schema>`,
{attr}`update_schema <fastapi_restly.views.BaseRestView.update_schema>` and
{attr}`response_schema <fastapi_restly.views.BaseRestView.response_schema>`.
[Generate the schema or write it](#write-or-generate) shows when to write the
schema yourself, and
[Hide a field from requests or from responses](#readonly-and-writeonly)
shows which fields each schema takes.

(configure-a-crud-view)=
## Configure a CRUD view

This table maps each need to its setting:

| You need | Configuration |
|---|---|
| Stable response fields | Declare {attr}`schema <fastapi_restly.views.BaseRestView.schema>` |
| Other fields or validation on create or update | Declare {attr}`create_schema <fastapi_restly.views.BaseRestView.create_schema>` or {attr}`update_schema <fastapi_restly.views.BaseRestView.update_schema>` ([Explicit write schemas](#explicit-write-schemas)) |
| Another type for the `{id}` path parameter | Set {attr}`id_type <fastapi_restly.views.BaseRestView.id_type>` ([Item paths](#item-route-matching)) |
| Fewer routes, such as a read-only view | Set the route method to `None`, such as `delete_endpoint = None` ([Leave out a route](#leave-out-routes)) |
| Hide rows from every read | Set {attr}`scope <fastapi_restly.views.BaseRestView.scope>`, or `default_scope` on the model ([Scopes](#view-scope)) |
| No pagination | Set {attr}`pagination <fastapi_restly.views.BaseRestView.pagination>` to `None`, or to a {class}`NoPagination <fastapi_restly.views.NoPagination>` to choose its envelope ([Filtering, Sorting & Pagination](#no-pagination)) |
| Other page limits, parameter names or list envelope | Set {attr}`pagination <fastapi_restly.views.BaseRestView.pagination>` to a {class}`NumberedPagination <fastapi_restly.views.NumberedPagination>` ([Pagination settings](#pagination-settings)) |
| A list with `limit` and `offset` | Set {attr}`pagination <fastapi_restly.views.BaseRestView.pagination>` to an {class}`OffsetPagination <fastapi_restly.views.OffsetPagination>` ([Page with limit and offset](#limit-offset-pagination)) |
| Fewer filter or sort keys on a list | Set {attr}`include_filters <fastapi_restly.views.BaseRestView.include_filters>` or {attr}`include_sort_keys <fastapi_restly.views.BaseRestView.include_sort_keys>` ([Choose the keys of a list](#choose-list-keys)) |
| A list filter of your own | Decorate a method with {func}`fr.list_filter <fastapi_restly.views.list_filter>` ([Add a filter of your own](#own-list-filter)) |
| A query key of your own next to the list params, not a filter | Declare it on the endpoint method or in a dependency ([Accept extra query parameters](#extra-query-parameters)) |
| OpenAPI metadata or dependencies on every route | Set {attr}`tags <fastapi_restly.views.View.tags>`, {attr}`responses <fastapi_restly.views.View.responses>` or {attr}`dependencies <fastapi_restly.views.View.dependencies>` ([Share behavior with base views](#share-behaviour-with-base-views)) |
| One option of a route, such as its name, status code or dependencies | Use {func}`fr.modify_route <fastapi_restly.views.modify_route>` under the name of the route method ([Change one option of a route](#modify-route)) |
| Another session for one view | Override the `session` annotation ([Database & Configuration](#session-dependency-one-view)) |

[View Reference](#view-class-attributes) lists the type and the default of
each attribute.

(modify-route)=
### Change one option of a route

To change one option of a route that a view inherits, use
{func}`fr.modify_route <fastapi_restly.views.modify_route>` under the name of
the route method. The route keeps its method and its other options:

```python
class InvoiceView(fr.AsyncRestView):
    prefix = "/invoices"
    model = Invoice
    schema = InvoiceSchema

    get_many_endpoint = fr.modify_route(
        summary="List invoices", operation_id="billing_list_invoices"
    )
    create_endpoint = fr.modify_route(status_code=200)
    update_endpoint = fr.modify_route(methods=["PUT"])
```

This changes the route of this view and its subclasses. The route of the base
class stays the same.

It takes the same arguments as a route decorator. The hooks of the view run as
before. Restly still fills in what you do not pass: the list params, the name,
the summary and the operation ID. When you pass `name`, FastAPI makes the
summary and the operation ID from that name, unless you pass them too. The
table in [The names and operation IDs of the CRUD routes](#default-route-metadata)
lists the endpoint methods.

:::{admonition} Details
:class: details

Each argument replaces the one of the inherited route. `dependencies` and
`responses` add up, as they do on a view. `dependencies` run base first, so a
subclass cannot remove a check of its base. `responses` add up per status code,
and a status code set to `None` is left out:

```python
    delete_endpoint = fr.modify_route(dependencies=[Depends(require_admin)])
    get_one_endpoint = fr.modify_route(responses={404: None})
```

FastAPI adds the 422 response itself, so `{422: None}` does not remove it.

`path` replaces the path of the route, as in `fr.modify_route("/new")`. Restly
also serves the list route and the create route at `/invoices/`, with a slash at
the end, hidden from OpenAPI. To leave out these hidden routes, set `path=""` on
the list route and on the create route. In a new path of an item route, write
`{id}`. A path such as `/{role_id}` raises an error when the view registers: the
method reads `id`, so nothing would read `role_id`.

`methods=["PUT"]` changes only the HTTP method. The route still updates only the
fields that the client sends. A React Admin view already has a `PUT` route,
`put`. So there, `methods=["PUT"]` on `update_endpoint` raises an error, because
two routes would have the same path and HTTP method. A status code without a
body, such as 204, also removes the response model of the route. For a delete
with a body, write the method.

`fr.modify_route` changes how a route is served, not what it sends. So
`response_model` and the other `response_model_*` arguments raise an error.
A route of the view builds its response with
{meth}`to_single_response <fastapi_restly.views.BaseRestView.to_single_response>`
or {meth}`to_list_response <fastapi_restly.views.BaseRestView.to_list_response>`.
Another response model would skip that step, and the fields that it hides would
go out. To change the response of the view, set `response_schema` or
`list_response_schema`. To send another schema from one route, write the method,
for example a schema with more fields on `GET /{id}`:

```python
    @fr.get("/{id}")
    async def get_one_endpoint(self, id: IDType) -> InvoiceDetailSchema:
        obj = await self.handle_get_one(id)
        return InvoiceDetailSchema.model_validate(obj)
```

Then the route decides what it sends, as a plain FastAPI route does.

An option does not change the order of the routes. So a route that the
class writes, such as `GET /stats`, stays before the item route.

A subclass inherits the options and can change them further. A plain class can
hold options for several views. Put it before the view class, as in
`class TagView(QuietRoutes, fr.AsyncRestView)`, as for any other method of a mixin.
After the view class, the options would do nothing, so Restly raises an error
when Python defines the class.

A name that is not a route of a base class raises
{class}`RestlyConfigurationError <fastapi_restly.exc.RestlyConfigurationError>`
when Python defines the class. Use `fr.modify_route` in the class body. An
argument that FastAPI does not know raises a `TypeError` when the view registers,
as it does on a route decorator.

A method with a route decorator states the whole route. So options from a base
class do not apply to it. Write such a method when the change needs code, such as
a decorator on the route or a response header. It can call the method of the base
class, so the route works as before:

```python
    @fr.post("/")
    async def create_endpoint(
        self, data: CreateSchema, response: Response
    ) -> ResponseSchema:
        obj = await super().create_endpoint(data)
        path = self.request.url.path.rstrip("/")
        response.headers["Location"] = f"{path}/{obj.id}"
        return obj
```

`ResponseSchema` keeps the view's response as the response model; see
[Name the view's schemas in a custom route](#name-the-view-schemas). An item
route that you declare, such as `GET /{id}`, keeps the path that you give it.
Restly adds the `int` or `uuid` converter only to a path parameter annotated
`IDType`; see [Item paths and static routes](#item-route-matching).
:::

(leave-out-routes)=
## Leave out a route

To leave out a route that the view inherits, set its method to `None`:

```python
@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    update_endpoint = None
    delete_endpoint = None
```

`UserView` now serves only `GET /users`, `POST /users` and `GET /users/{id}`.

A subclass inherits the `None`. To make several views read-only, set it on a
base view:

```python
class ReadOnlyView(fr.AsyncRestView):
    create_endpoint = None
    update_endpoint = None
    delete_endpoint = None


@fr.include_view(app)
class ProductView(ReadOnlyView):
    prefix = "/products"
    model = Product
```

`ProductView` only serves `GET /products` and `GET /products/{id}`.

A subclass leaves out only the routes that it sets to `None`. So it also
serves the custom routes of its base, such as `POST /tags/merge`, and also a
write route that someone adds to the base later. For a read-only copy of a
view with custom routes, put the shared settings in a base view, and subclass
it twice. Then the copy does not inherit the custom routes of `TagView`:

```python
class TagBase(fr.AsyncRestView):
    model = Tag


@fr.include_view(app)
class TagView(TagBase):
    prefix = "/tags"

    @fr.post("/merge")
    async def merge(self, source_id: int, target_id: int) -> None: ...


@fr.include_view(app)
class PublicTagView(TagBase):
    prefix = "/public/tags"
    create_endpoint = None
    update_endpoint = None
    delete_endpoint = None
```

`PublicTagView` serves only the two read routes, also when `TagView` gets more
routes later. But both views serve a route that `TagBase` gets later.

:::{admonition} Details
:class: details

`None` works the same for a custom route, such as `merge = None`, and on a
plain {class}`fr.View <fastapi_restly.views.View>`. Restly does not check the
name, so a typo such as `delte_endpoint = None` leaves out nothing.
`fr.modify_route` on a route that a base sets to `None` raises an error,
because there is no route to change.
:::

(crud-limits)=
## What the default routes do not do

The default CRUD routes have these limits:

- A row is identified by one primary key column. The `{id}` path parameter
  takes the Python type of the key, so a UUID or string key needs no
  configuration. Reference fields such as `fr.MustExist[UUID, Model]` accept
  these keys too. For a composite key, set `id_type = str` and override
  `where_id` to read the parts of the key from the path; see
  [Look a row up by another key](#natural-key-route). Without `where_id`, a
  view of such a model fails when it registers, unless it leaves out the
  item routes.
- Responses can contain nested objects, and lists can filter on
  relationships. Request bodies with nested objects are not supported in
  general. Use `MustExist`, `IDRef` or `IDSchema` to refer to other rows, or
  change the payload in a business method; see
  [Relationships](relationships.md).
- The update route uses `PATCH`, not `PUT`. Add a `PUT` route only when the
  client needs one; see [Replace `update_endpoint`](#replace-update-endpoint).

## See also

- [Build a Blog API](tutorial.md): build and run a first resource.
- [Update](crud_update.md): the steps of `PATCH /{id}` and what you can
  change.
- [Request Lifecycle & Hooks](lifecycle.md): the business methods, the
  commit and its hooks, and custom routes.
- [Nested Routes](nested_routes.md): serve a child resource under its
  parent.
- [Schemas & Validation](schemas.md): the fields that the routes
  accept and return.
- [Views](views.md): registration, shared dependencies and base
  views.
- [View Reference](view_reference.md): the exact signatures and defaults.
