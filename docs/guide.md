# Guide

The Guide has one page for each part of Restly, in five groups. Each page
starts with how that part works, then shows how to use it. If you are new to
Restly, start with the tutorial, [Build a Blog API](tutorial.md).

(how-restly-works)=

## How Restly works

The goal of Restly is less boilerplate and more consistency.

In Restly, you write your API as view classes. A view is a class that groups
related FastAPI routes, for example all routes under `/projects`. The base
class, {class}`fr.View <fastapi_restly.views.View>`, is for your own set of
reusable endpoints. It has no predefined routes. See
[Group endpoints that are not CRUD](#when-to-use-view-directly).

{class}`fr.RestView <fastapi_restly.views.RestView>` is a view that already
has five CRUD routes. You give it a SQLAlchemy model, and it serves routes to
list, read, create, update and delete rows of that model. `RestView` has an
async version, {class}`fr.AsyncRestView <fastapi_restly.views.AsyncRestView>`.
These docs say `RestView` for both.

```python
@fr.include_view(app)
class ProjectView(fr.AsyncRestView):
    prefix = "/projects"
    model = Project  # your SQLAlchemy model
```

Each route of a `RestView` has two layers that you can change:

- **The HTTP layer**: one endpoint method for each route, such as
  `update_endpoint`. It holds what is about HTTP: the path, the parameters
  and the response. To change one option of a route, such as the status
  code, use {func}`fr.modify_route <fastapi_restly.views.modify_route>`. To
  change more, replace the endpoint method. See
  [Change one option of a route](#modify-route).
- **The business layer**: business methods, such as `create`, `update` and
  `delete`. They make the change to the data. Override one to change what a
  route does, for example to hash a password or to set a field. See
  [Change what an action does](#business-methods).

Between the two layers, Restly runs a fixed set of steps, such as loading a
row from the database, checking access and committing.
[How a CRUD route runs](#view-request-steps) shows all steps in order, and
which ones you can override.

Some things you write only once:

- **Fields**: you list the fields that clients see in one Pydantic schema, and
  Restly builds the create, update and response schemas from it. If you do not
  write this schema, Restly makes one from the model.
- **Access**: you write once which rows a caller can see, as a scope, and
  Restly applies it to every read of the model.
- **Shared behavior**: behavior that several views share goes in a base view
  that they inherit from.

Underneath, Restly is plain FastAPI, SQLAlchemy and Pydantic. Your model stays
your own SQLAlchemy model, the routes are FastAPI routes, and the schemas are
Pydantic classes. When a route does not fit Restly, write it as a normal
FastAPI route next to your views.

## Setup

Restly adds views to a FastAPI app, a new one or one that you already have.
It needs one call to {func}`fr.configure() <fastapi_restly.db.configure>`,
which tells it where its database sessions come from. Your models stay
ordinary SQLAlchemy models on a declarative base of your own.

- [New Projects](new_projects.md): start a project with `restly new`, lay
  it out as it grows, and serve more than one API version.
- [Existing Projects](existing_projects.md): add Restly to an app that you
  already have, share its login check and its `get_db`, keep its engine and
  models, and replace a hand-written router with a view.
- [Database & Configuration](database.md): tell Restly where its database
  is, reuse the engine, sessionmaker or session generator that your app
  already has, use another database on one view, such as a read replica, and
  open a session outside a request.
- [Models](models.md): add Restly's defaults and common columns to your
  models, and set the values that every write needs, such as the author or a
  slug.

## Views & Routes

A view is a class that groups related endpoints under one URL prefix.
[View Reference](view_reference.md) lists everything that you can set,
override or call on a view.

- [Views](views.md): the view classes, how to declare routes, add a decorator
  and register a view, routes that are not CRUD, such as login and webhooks, and
  how views share behavior through base classes.
- [React Admin](react_admin.md): connect react-admin to a view, the list
  format and the routes that react-admin expects, to-many id arrays, CORS,
  and a list that shows no rows.
- [Type Annotations](typing.md): type the schema, the methods that you
  override and your custom routes.

## CRUD Routes

A CRUD view serves five routes for one model by default: list, get one,
create, update and delete. Each route runs a fixed list of steps, such as
`authorize` and the commit. Each route has a page that shows its steps in
order, and what you can change, from a rule on the schema to a route of your
own.

- [Overview](crud_routes.md): the five default routes, how a route runs,
  async or sync, how to change one option of a route, and how to leave some
  routes out.
- [Update](crud_update.md): the steps of `PATCH /{id}`, what you can change,
  the `update` method, a `PUT` route, and an update from a custom route.
- [Request Lifecycle & Hooks](lifecycle.md): which method to override for
  each change, a value on each row, a lookup by another key, custom routes,
  what commits when, code that must commit, several writes in one commit, and
  the same logic in a script or a worker.
- [Nested Routes](nested_routes.md): serve a child resource under its
  parent, create a child, limit the parents that a client can reach, and nest
  more than one level.

## Requests & Responses

A view has several schemas. The main one is the view's
{attr}`schema <fastapi_restly.views.BaseRestView.schema>`, a Pydantic class
that lists the fields of the resource. Restly builds the other schemas
from it: the response, create and update schemas. So the main schema
decides what clients send and what they get back. The list route adds
filter, sort and page parameters to the URL.

- [Schemas & Validation](schemas.md): generate the schema or write it, keep
  a field out of the requests or the responses, validate input, keep times
  in UTC, keep NaN and infinity, store a Pydantic model in a JSON column, use
  other field names in JSON, such as camelCase, and build a class for each view
  with `SchemaType`.
- [Relationships](relationships.md): accept the id of another row, check
  that the row exists, link many rows to many rows, return related objects,
  and fix `MissingGreenlet`.
- [Responses](responses.md): what each route returns, the list envelope and
  the list response schema, the response of a custom route, and how to change
  the shape of one route or of all routes.
- [Filtering, Sorting & Pagination](filtering.md): the filters, the sort
  order and the pages that a client can use on a list, the keys that a view
  chooses, filters of your own, and list params that you write yourself.
- [Errors](errors.md): raise a 404 or a 403, see why a duplicate value gives
  a 409, and change the error body.
- [OpenAPI](openapi.md): the tags, names and operation IDs of the routes, and
  how to document a custom route.

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
  the app, set up Alembic, add a health check, and check the list settings
  before an API goes public.

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

new_projects
existing_projects
database
models
```

```{toctree}
:caption: Views & Routes
:maxdepth: 1
:hidden:

views
react_admin
typing
```

```{toctree}
:caption: CRUD Routes
:maxdepth: 1
:hidden:

Overview <crud_routes>
crud_update
lifecycle
nested_routes
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
