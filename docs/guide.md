# Guide

The Guide has one page for each part of Restly, in five groups. Each page
starts with how that part works, then shows how to use it. If you are new to
Restly, start with the tutorial, [Build a Blog API](tutorial.md).

## Setup

Restly adds views to a FastAPI app, a new one or one that you already have.
It needs one call to {func}`fr.configure() <fastapi_restly.db.configure>`,
which tells it where its database sessions come from. Your models stay
ordinary SQLAlchemy models on a declarative base of your own.

- [New & Existing Projects](projects.md): add Restly to an app that you
  already have, start a new project with `restly new`, lay out a project as
  it grows, and serve more than one API version.
- [Database & Configuration](database.md): tell Restly where its database
  is, reuse the engine or sessionmaker that your app already has, use another
  database on one view, such as a read replica, and open a session outside a
  request.
- [Models](models.md): add Restly's defaults and common columns to your
  models, and set the values that every write needs, such as the author or a
  slug.

## Views & Routes

A view is a class that groups related endpoints. A CRUD view serves five
routes for one model by default. Each route runs a fixed list of steps, such
as `authorize` and `create`. To change a route, you override the method of
one step.

- [Views](views.md): the view classes, how to declare routes and register a
  view, routes that are not CRUD, such as login and webhooks, and how views
  share behavior through base classes.
- [CRUD Routes](crud_routes.md): the five default routes, async or sync, how
  to leave some routes out, and how to serve a child resource under its
  parent.
- [Request Lifecycle & Hooks](lifecycle.md): the steps of a request, which
  method to override for each change, custom routes, what commits when,
  several writes in one commit, and the same logic in a script or a worker.
- [React Admin](react_admin.md): views that speak the REST dialect of
  react-admin.
- [Type Annotations](typing.md): type the schema, the methods that you
  override and your custom routes.

## Requests & Responses

A view has one schema, a Pydantic class that lists the fields of the
resource. Restly builds the request bodies and the response class from it,
so one schema decides what clients send and what they get back. The list
route adds filter, sort and page parameters to the URL.

- [Schemas & Validation](schemas.md): generate the schema or write it, keep
  a field out of the requests or the responses, validate input, and use other
  field names in JSON, such as camelCase.
- [Relationships](relationships.md): accept the id of another row, check
  that the row exists, return related objects, and fix `MissingGreenlet`.
- [Responses](responses.md): what each route returns, the list envelope,
  and how to change the shape of one route or of all routes.
- [Filtering & Pagination](filtering.md): the filters, the sort order and
  the pages that a client can use on a list.
- [Errors](errors.md): raise a 404 or a 403, see why a duplicate value gives
  a 409, and change the error body.
- [OpenAPI](openapi.md): the tags, names and operation IDs of the routes,
  and how to document a custom route.

## Access

These pages make the caller available to your code, and decide what the
caller may do and which rows the caller sees. [`Current`](current.md) holds
the caller during a request.
{meth}`authorize <fastapi_restly.views.RestView.authorize>` decides whether
the caller may do an action, so a refusal gives 403. A scope decides which
rows exist, so a hidden row gives 404.

- [Current & ContextParam](current.md): bind the current user in a
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
  views, see [Current & ContextParam](#current-request-binding).
- [Middleware](https://fastapi.tiangolo.com/tutorial/middleware/).
- [CORS](https://fastapi.tiangolo.com/tutorial/cors/). For a react-admin
  frontend, see also [CORS for React Admin](#react-admin-cors).
- [Background tasks](https://fastapi.tiangolo.com/tutorial/background-tasks/).
  For work that must still happen after a crash, use an outbox row in a
  [transaction hook](#after-action-commit-raises).
- [File uploads](https://fastapi.tiangolo.com/tutorial/request-files/).
- [WebSockets](https://fastapi.tiangolo.com/advanced/websockets/). Declare
  them on the app or on a router, not on a view.

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

current
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
