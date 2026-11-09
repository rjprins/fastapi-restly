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
steps. Each route also has a page of its own: [List](crud_list.md),
[Get One](crud_get_one.md), [Create](crud_create.md), [Update](crud_update.md)
and [Delete](crud_delete.md). It shows the steps of that route in order, and a
table of what you can change.

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
| [`GET /users`](crud_list.md) | {meth}`get_many_endpoint <fastapi_restly.views.AsyncRestView.get_many_endpoint>` | Filter, sort and page parameters | A page of objects in a `data` envelope | `200` |
| [`POST /users`](crud_create.md) | {meth}`create_endpoint <fastapi_restly.views.AsyncRestView.create_endpoint>` | `create_schema` | `response_schema` | `201` |
| [`GET /users/{id}`](crud_get_one.md) | {meth}`get_one_endpoint <fastapi_restly.views.AsyncRestView.get_one_endpoint>` | The id in the path | `response_schema` | `200` |
| [`PATCH /users/{id}`](crud_update.md) | {meth}`update_endpoint <fastapi_restly.views.AsyncRestView.update_endpoint>` | `update_schema` | `response_schema` | `200` |
| [`DELETE /users/{id}`](crud_delete.md) | {meth}`delete_endpoint <fastapi_restly.views.AsyncRestView.delete_endpoint>` | The id in the path | Empty body | `204` |

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
[Replace an endpoint method](#replace-endpoint-method).

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
**handler**. The handler runs the steps, and calls
{meth}`create <fastapi_restly.views.RestView.create>`, the **business
method**:

```{code-block} text
POST /
  └─ create_endpoint(data)          # endpoint method: the HTTP side
       ├─ handle_create(data)       # handler: runs the steps (final)
       │    ├─ authorize, check the references in data
       │    ├─ create(data)         # business method: builds the object
       │    └─ save, before_action_commit, commit, after_action_commit
       └─ to_single_response(obj)   # the response
```

The page of each route, such as [Create](crud_create.md), shows all the steps
of that route, and marks the methods that you can override.

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
  [Replace an endpoint method](#replace-endpoint-method).
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
  the loader options and two small hooks. See
  [Visibility, then policy](#read-request-steps).

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

The save writes every change in the session. It refreshes the object that the
business method returns, and every other row of the view's model that the
action wrote. A row of another model that the method changes, such as a line
of an order, is written but not refreshed. When the change set a foreign key
column of such a row, such as `line.product_id`, the save unloads that
relationship. So a response that nests the line loads the new product, not
the old one. A business method that you call yourself, outside a handler or a
`write_action` block, writes nothing: the handler does the save.
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

Most changes to a CRUD view are a class attribute. The page of each route
lists the changes for that route; this table lists the settings for the
whole view:

| Your change | Setting |
|---|---|
| Fixed fields in the responses | [`schema`](#write-or-generate) |
| Other fields on create or update | [`create_schema`, `update_schema`](#explicit-write-schemas) |
| The type of `{id}` in the path | [`id_type`](#item-route-matching) |
| Fewer routes | [`delete_endpoint = None`](#leave-out-routes) |
| Hide rows from every read | [`scope`](#view-scope) |
| Pages of the list | [`pagination`](#list-pagination) |
| No pages | [`pagination = None`](#no-pagination) |
| Pages with `limit` and `offset` | [`fr.OffsetPagination`](#limit-offset-pagination) |
| Fewer filter or sort keys | [`include_filters`](#choose-list-keys) |
| A filter of your own | [`fr.list_filter`](#own-list-filter) |
| A query key that is not a filter | [A parameter](#extra-query-parameters) |
| Tags or dependencies on every route | [`tags`, `dependencies`](#share-behaviour-with-base-views) |
| One option of one route | [`fr.modify_route`](#modify-route) |
| Another session for one view | [`session`](#session-dependency-one-view) |

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

(business-methods)=
## Change what a write does

Each write has a business method:
{meth}`create <fastapi_restly.views.RestView.create>`,
{meth}`update <fastapi_restly.views.RestView.update>` or
{meth}`delete <fastapi_restly.views.RestView.delete>`. Override only the
one you need. The handler still checks permissions and commits around it.
The page of each write shows its business method in full:
[Create](#create-method), [Update](#update-method) and
[Delete](#delete-method).

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
  [commit hook](#transaction-hooks), for an event about an action of the
  view. An event that every write path must send needs a SQLAlchemy session
  event instead.

(replace-endpoint-method)=
## Replace an endpoint method

Business methods and hooks keep the HTTP side of a route: its path, its
parameters and its response. To change only an option of the route, such as
its status code, use
{func}`fr.modify_route <fastapi_restly.views.modify_route>`; see
[Change one option of a route](#modify-route). Replace the endpoint method
when the change needs code: the headers, the parameters or what the route
returns.

Define a method with the same name, such as `delete_endpoint`, and give it a
route decorator. Keep the call to the handler, so the steps of the route
still run. The page of each route has an example:
[List](#replace-get-many-endpoint), [Get One](#replace-get-one-endpoint),
[Create](#replace-create-endpoint), [Update](#replace-update-endpoint) and
[Delete](#replace-delete-endpoint). To change the shape of the response body,
such as an envelope around one object, see
[Change the shape of one route](#one-route-shape).

When several views need the same replacement, put it in a mixin. List the
mixin before the view class, so Python finds its method first:

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
```

`IDType` and `ResponseSchema` give each view that uses the mixin its own id
type and response schema. The [React Admin](react_admin.md) views use this
pattern. They replace
{meth}`get_many_endpoint <fastapi_restly.views.RestView.get_many_endpoint>`
for the `ra-data-simple-rest` contract, and keep the handlers, the loads and
the business methods.

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

(view-self-attributes)=
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


(crud-limits)=
## What the default routes do not do

The default CRUD routes have these limits:

- A row is identified by one primary key column. The `{id}` path parameter
  takes the Python type of the key, so a UUID or string key needs no
  configuration. Reference fields such as `fr.MustExist[UUID, Model]` accept
  these keys too. For a composite key, set `id_type = str` and override
  `where_id` to read the parts of the key from the path; see
  [Find the row by another key](#natural-key-route). Without `where_id`, a
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
- [Custom Routes](custom_routes.md): routes of your own on a CRUD view.
- [Commits & Hooks](commits.md): what commits, and code before or after the
  commit.
- [Nested Routes](nested_routes.md): serve a child resource under its
  parent.
- [Schemas & Validation](schemas.md): the fields that the routes
  accept and return.
- [Views](views.md): registration, shared dependencies and base
  views.
- [View Reference](view_reference.md): the exact signatures and defaults.
