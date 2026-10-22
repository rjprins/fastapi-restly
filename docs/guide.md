# Guide

The Guide has one page for each part of Restly, in five groups. Each page
starts with how that part works, then shows how to use it. If you are new to
Restly, start with the tutorial, [Build a Blog API](tutorial.md).

(how-restly-works)=

## How Restly works

You write a SQLAlchemy model and a view class, and Restly turns the view into
FastAPI routes. For example, when a client sends `PATCH /projects/1`,
FastAPI first checks the request body against the update schema,
`ProjectUpdate`. Then these steps run:

```
PATCH /projects/1
  └─ update_endpoint(id, data)            # endpoint method: HTTP contract
       ├─ handle_update(id, data)         # handler: runs the steps
       │    ├─ get_one(id)                # loads through the scope, or 404
       │    ├─ authorize("update", ...)   # may the caller do this? Or 403
       │    ├─ check the references       # an id that does not exist: 404
       │    ├─ update(obj, data)          # business method: the change
       │    ├─ save                       # flush and refresh the row
       │    ├─ before_action_commit(...)
       │    ├─ commit
       │    └─ after_action_commit(...)
       └─ to_response(obj)                # the response class, as JSON
```

These are the parts, and the page that explains each one:

- **Model**: your SQLAlchemy model, on a declarative base of your own.
  Restly works with it, but does not own it. See [Models](models.md).
- **View**: a class that groups related endpoints under a `prefix`.
  {func}`fr.include_view() <fastapi_restly.views.include_view>` registers its
  routes on an app or a router. A CRUD view,
  {class}`fr.AsyncRestView <fastapi_restly.views.AsyncRestView>` or
  {class}`fr.RestView <fastapi_restly.views.RestView>`, also names a `model`,
  and serves five routes for it. See [Views](views.md) and
  [CRUD Routes](crud_routes.md).
- **The view's schema**: a Pydantic class that lists the fields of the
  resource. Restly builds the request bodies, such as `ProjectUpdate`, and the
  response class, `ProjectResponse`, from it. A view without a schema gets one
  that Restly generates from the model. See
  [Schemas & Validation](schemas.md).
- **Endpoint method, handler and business method**: the three layers of each
  CRUD route. To change what an action does, override the business method,
  such as `update`. A read has a final load, `get_one` or `get_many`, in
  place of a business method: the scope and the loader options decide what
  it reads. To change the HTTP contract, such as the status code, replace
  the endpoint method. A handler is final. See
  [Request Lifecycle & Hooks](lifecycle.md).
- **Commit**: a write route commits after the business method.
  `before_action_commit` runs just before the commit, and
  `after_action_commit` just after it. A read does not commit. See
  [What commits, and when](#what-commits).
- **Scope**: a SQL where clause that limits which rows of a model a request
  can read. Restly applies it to every read of the model and to every
  reference to the model, so a row outside the scope is a 404. You write a
  scope as a query clause, a named rule that you can reuse. See
  [Scopes](scopes.md) and [Query Clauses](clauses.md).
- **`authorize`**: decides whether the caller may do the action. A refusal
  is a 403. See [Authorization](authorization.md).
- **`Current`**: the request context. It holds the caller and other values
  for the request. Scopes, column defaults and your `authorize` can read it.
  See [Request Context](context.md).
- **List params**: the filter, sort and page parameters of the list route.
  Restly builds them from the response class. See
  [Filtering, Sorting & Pagination](filtering.md).
- **Session**: each request gets a database session from the source that
  {func}`fr.configure() <fastapi_restly.db.configure>` sets. See
  [Database & Configuration](database.md).

## Setup

Restly adds views to a FastAPI app, a new one or one that you already have.
It needs one call to {func}`fr.configure() <fastapi_restly.db.configure>`,
which tells it where its database sessions come from. Your models stay
ordinary SQLAlchemy models on a declarative base of your own.

- [New & Existing Projects](projects.md): add Restly to an app that you
  already have, start a new project with `restly new`, lay out a project as
  it grows, and serve more than one API version.
- [Database & Configuration](database.md): tell Restly where its database
  is, reuse the engine, sessionmaker or session generator that your app
  already has, use another database on one view, such as a read replica, and
  open a session outside a request.
- [Models](models.md): add Restly's defaults and common columns to your
  models, and set the values that every write needs, such as the author or a
  slug.

## Views & Routes

A view is a class that groups related endpoints. A CRUD view serves five
routes for one model by default. Each route runs a fixed list of steps, such
as `authorize` and `create`. To change a route, you override the method of
one step. [View Reference](view_reference.md) lists everything that you can
set, override or call on a view.

- [Views](views.md): the view classes, how to declare routes and register a
  view, routes that are not CRUD, such as login and webhooks, and how views
  share behavior through base classes.
- [CRUD Routes](crud_routes.md): the five default routes, async or sync, how
  to leave some routes out, and how to serve a child resource under its
  parent.
- [Request Lifecycle & Hooks](lifecycle.md): the steps of a request, which
  method to override for each change, a value on each row, a lookup by
  another key, custom routes, what commits when, several writes in one
  commit, and the same logic in a script or a worker.
- [React Admin](react_admin.md): connect react-admin to a view, the list
  format and the routes that react-admin expects, to-many id arrays, CORS,
  and a list that shows no rows.
- [Type Annotations](typing.md): type the schema, the methods that you
  override and your custom routes.

## Requests & Responses

A view has several schemas. The main one is the view's
{attr}`schema <fastapi_restly.views.BaseRestView.schema>`, a Pydantic class
that lists the fields of the resource. Restly builds the other schemas
from it: the request bodies and the response class. So the view's schema
decides what clients send and what they get back. The list route adds
filter, sort and page parameters to the URL.

- [Schemas & Validation](schemas.md): generate the schema or write it, keep
  a field out of the requests or the responses, validate input, and use other
  field names in JSON, such as camelCase.
- [Relationships](relationships.md): accept the id of another row, check
  that the row exists, link many rows to many rows, return related objects,
  and fix `MissingGreenlet`.
- [Responses](responses.md): what each route returns, the list envelope,
  the response of a custom route, and how to change the shape of one route
  or of all routes.
- [Filtering, Sorting & Pagination](filtering.md): the filters, the sort
  order and the pages that a client can use on a list, the keys that a view
  chooses, filters of your own, and list params that you write yourself.
- [Errors](errors.md): raise a 404 or a 403, see why a duplicate value gives
  a 409, and change the error body.
- [OpenAPI](openapi.md): the tags, names and operation IDs of the routes,
  and how to document a custom route.

## Access

These pages cover how your code gets the caller, what the caller may do,
and which rows the caller sees. [`Current`](context.md) holds
the caller during a request.
{meth}`authorize <fastapi_restly.views.RestView.authorize>` decides whether
the caller may do an action, so a refusal gives 403. A scope decides which
rows exist, so a hidden row gives 404.

- [Request Context](context.md): bind the current user in a
  dependency, a script or a test.
- [Authorization](authorization.md): allow or refuse each action, for each
  caller.
- [Query Clauses](clauses.md): name a query rule once, then reuse and
  combine it.
- [Scopes](scopes.md): hide rows from every read, show each user only their
  own rows, and soft-delete rows.
- [Multi-Tenancy](tenancy.md): keep the rows of each tenant apart.

## Testing & Deploying

Restly ships a pytest plugin that gives each test a clean database and
clients for your app. A Restly app deploys like any FastAPI app, so only the
parts that Restly adds need their own page.

- [Testing](testing.md): set up the test suite, build the test schema, and
  use the test clients and fixtures.
- [Deploying](deploying.md): read the settings from the environment, run
  the app, set up Alembic and add a health check.

## What FastAPI covers

Restly runs inside a FastAPI app, and
{func}`include_view <fastapi_restly.views.include_view>` registers plain HTTP
routes. So these subjects are FastAPI's, and its docs explain them:

- [Security](https://fastapi.tiangolo.com/tutorial/security/): OAuth2, API
  keys and other security schemes. To make the caller available to your
  views, see [Request Context](#current-request-binding).
- [Middleware](https://fastapi.tiangolo.com/tutorial/middleware/).
- [CORS](https://fastapi.tiangolo.com/tutorial/cors/). For a react-admin
  frontend, see also [CORS for React Admin](#react-admin-cors).
- [Background tasks](https://fastapi.tiangolo.com/tutorial/background-tasks/).
  For work that must still happen after a crash, use an outbox row in a
  [transaction hook](#after-action-commit-raises).
- [File uploads](https://fastapi.tiangolo.com/tutorial/request-files/).
- [WebSockets](https://fastapi.tiangolo.com/advanced/websockets/). Declare
  them on the app or on a router, not on a view: a view has HTTP routes
  only.

```{toctree}
:caption: Setup
:maxdepth: 1
:hidden:

projects
database
models
```

```{toctree}
:caption: Views & Routes
:maxdepth: 1
:hidden:

views
crud_routes
lifecycle
react_admin
typing
```

```{toctree}
:caption: Requests & Responses
:maxdepth: 1
:hidden:

schemas
relationships
responses
filtering
errors
openapi
```

```{toctree}
:caption: Access
:maxdepth: 1
:hidden:

context
authorization
clauses
scopes
tenancy
```

```{toctree}
:caption: Testing & Deploying
:maxdepth: 1
:hidden:

testing
deploying
```
