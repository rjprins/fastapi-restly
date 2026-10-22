# New & Existing Projects

Restly adds views to a FastAPI application. It does not replace the
application, and it does not need its own project layout. Three facts hold
for a new project and for an existing one:

- {func}`fr.include_view() <fastapi_restly.views.include_view>` registers the
  routes of a view on any `FastAPI` app or `APIRouter`, next to the routes
  that are already there.
- There is no global Restly router. A view has routes only where you call
  `fr.include_view()`.
- There is no autodiscovery. Nothing scans your packages for views or models.
  You import each view and register it yourself.

Restly also needs one call to
{func}`fr.configure() <fastapi_restly.db.configure>` at startup, which tells it
where its database sessions come from; see
[Database & Configuration](#configure-restly).

So any set of modules that Python can import works. The first part of this
page adds Restly to an application that you already have. The second part
shows the layout that we recommend for a new project, which `restly new`
generates.

(add-to-existing-app)=

## Add a view next to your existing routes

Call `fr.include_view()` where you already compose your routes. Your
existing routers and the new views can share the same app or router. They
also share your login check and your database session:

```python
from fastapi import APIRouter, Depends, FastAPI
import fastapi_restly as fr

from .auth import require_login       # the login check your routes already use
from .db import get_db                # the session generator your routes already use
from .orders import router as orders_router

app = FastAPI()
fr.configure(app, session_generator=get_db)

api = APIRouter(prefix="/api")

api.include_router(orders_router, prefix="/orders")  # existing FastAPI routes


class UserView(fr.AsyncRestView):
    prefix = "/users"
    dependencies = [Depends(require_login)]
    model = User
    schema = UserSchema


fr.include_view(api, UserView)  # registers the /api/users CRUD routes

app.include_router(api)
```

You adopt Restly one resource at a time. In the example, the orders routes
stay hand-written, and the users routes come from `UserView`. If you add a
`ProductView` later, the orders router does not change.

`require_login` runs before every route of the view; see
[Run a dependency on every route](#run-a-dependency-on-every-route).
FastAPI calls `get_db` once for each request, so the view and your
dependencies that use `Depends(get_db)`, such as `require_login`, get the
same session. An override of `get_db` in `app.dependency_overrides` gives the
views their session too, so a test setup that overrides `get_db` keeps
working; see
[Use the view's session in your own dependencies](#share-request-session).
A sync app passes its generator as `sync_session_generator`, and its views
are `fr.RestView`. Before you pass `get_db`, check the two rules in the next
section.

:::{admonition} Details
:class: details

`fr.include_view()` also works as a class decorator;
[Views](#register-a-view) explains both forms. Pass `app` to
`fr.configure()`, also when the views are on a router. That call checks the
OpenAPI schema of the app for two classes with the same name, and
`fr.include_view()` on a router cannot do this; see
[Database & Configuration](#configure-restly). The check also covers the
routes that you had before Restly. So the first warning can name two classes
of your own, which OpenAPI already showed under long names; see
[Name your schemas](#name-your-schemas).

A plain FastAPI route can also sit next to a view, for an endpoint that is not
part of the CRUD routes:

```python
@api.get("/users/{id}/export")
async def export_user(id: int):
    ...
```
:::

(existing-get-db)=

## Two rules for your `get_db`

A `get_db` that works for your own routes can still break the views.

Create its sessionmaker with `expire_on_commit=False`, and for async views
also with `autoflush=False`, as Restly does. Restly reads the response from
the object after the commit. With SQLAlchemy's default,
`expire_on_commit=True`, that read fails in an async view with
`MissingGreenlet`, so every `POST` and `PATCH` answers 500. A sync view still
works, but `snapshot(new)` in `after_action_commit` is empty, and Restly
warns:

```python
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
```

And `get_db` must not commit. A `get_db` that commits after the `yield` saves
changes that should be rolled back, such as those of a route that forgot to
commit. Remove that commit:

```python
async def get_db():
    async with SessionLocal() as session:
        yield session
        # no session.commit() here
```

:::{warning}
First add a commit to each of your own routes that writes. A route that
still relies on the commit in `get_db` answers 200, but its writes are lost,
and nothing warns you.
:::

[Build sessions with your own generator](#own-session-generator) has the
details of both rules.

(hand-written-crud-route)=

## Write one CRUD route by hand

To write one of the default CRUD routes yourself, leave it out of the view
with [`exclude_routes`](#exclude-crud-routes) and add your own FastAPI route:

```python
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserSchema
    exclude_routes = (fr.ViewRoute.DELETE,)


fr.include_view(api, UserView)


@api.delete("/users/{id}", status_code=204)
async def delete_user(id: int):
    ...
```

`UserView` still serves list, create, read and update. Your FastAPI route
serves DELETE. When the HTTP contract stays the same and only the logic
changes, override a business method instead, such as
{meth}`delete <fastapi_restly.views.RestView.delete>`. For a different status
code, response shape or query interface, see
[Request Lifecycle & Hooks](#which-method-to-override).

(move-back-to-fastapi)=

## Move a resource back to plain FastAPI

A resource has Restly routes only where you call `fr.include_view()`. To move
it back to plain FastAPI, remove that call and register an `APIRouter` with
the same prefix and path operations. Your models, schemas, dependencies and
session setup can stay where they are.

(replace-hand-written-router)=

## Replace a hand-written router with a view

Before you replace a hand-written CRUD router with a
{class}`RestView <fastapi_restly.views.RestView>`, compare the two HTTP
contracts:

1. Map your routes to the default CRUD routes of the view: `GET /`, `POST /`,
   and `GET`, `PATCH` and `DELETE` on `/{id}`
   ([the exact contract](#default-crud-behavior)). Other routes on the
   router, such as exports and actions, become custom routes on the view or
   stay as plain FastAPI routes next to it.
2. Keep routes with a different contract out of the change. For example, a
   `PUT` update or a delete that does not answer 204 can stay hand-written:
   leave it out with
   {attr}`exclude_routes <fastapi_restly.views.BaseRestView.exclude_routes>`
   until you change its contract.
3. Write tests before you change anything. Test the responses of the old
   router with [`RestlyTestClient`](#test-clients). Then replace the router
   with the view and run the same tests again. A changed payload or status
   code then shows at once.

A resource in the middle of this change looks like this:

```python
class ProductView(fr.AsyncRestView):
    prefix = "/products"
    model = Product
    schema = ProductSchema          # match your old response shape exactly
    exclude_routes = (fr.ViewRoute.DELETE,)  # the old DELETE returns the object


fr.include_view(api, ProductView)
api.include_router(legacy_delete_router)  # until the contract is changed
```

## Keep your engine and your models

Restly can use the database setup and the models that your application
already has:

- [Reuse your existing engine or sessionmaker](#reuse-existing-engine)
  shows how to give your engine to `fr.configure()`, for an app that has no
  session generator.
- [Use your existing models](#existing-models) shows what Restly needs from
  your models.

(restly-new)=

## Generate a new project

`restly new` creates a project with a `users` resource, a test suite, and the
setup for settings and the database schema:

```bash
pip install fastapi-restly
restly new myapp
```

The name is used for the directory, for `pyproject.toml` and for the
databases. The package inside is always `app`, so
`from app.settings import Settings` is the same line in every Restly project.

There are three choices. Each one is a flag, and on a terminal the command
asks for each flag that you leave out:

| | default | alternative |
|---|---|---|
| Views and driver | `--async` | `--sync` |
| Database | `--postgres`, with a `compose.yaml` | `--sqlite` |
| Schema | `--alembic` | `--create-all`, built at startup and in tests |

[example-projects/starter](https://github.com/rjprins/fastapi-restly/tree/main/example-projects/starter)
is exactly what `restly new myapp` writes with the default answers: async
views, PostgreSQL and Alembic. Read it to see the recommended layout at its
smallest. A check in CI keeps it equal to the output of the generator. It
uses the released version of Restly, not this repository, so local changes to
Restly do not show in it.

## Start with one file

A single module is the right shape for a first resource.
[Build a Blog API](tutorial.md) builds a complete application in one file,
and splitting that file would not help while it stays that small.

Split it when the second or third resource arrives, or when one resource gets
explicit schemas and several overrides.

(project-layout)=

## Organize by subject, then by type

Give each resource a package, and name the modules inside it after the kind
of code that they hold:

```text
app/
├── main.py                 # Application factory, VIEWS, and lifespan
├── asgi.py                 # app = create_app(), the deployment entrypoint
├── settings.py             # Pydantic settings
├── current.py              # Current namespace (optional)
├── models.py               # Declarative base and shared model mixins
├── views.py                # Shared base view and mixins
├── users/
│   ├── __init__.py
│   ├── models.py
│   ├── schemas.py
│   └── views.py
└── tasks/
    ├── __init__.py
    ├── models.py
    ├── schemas.py
    └── views.py
```

The subject comes first, and the type of code second. This matters once the
application is large enough. The
[SaaS example](https://github.com/rjprins/fastapi-restly/tree/main/example-projects/saas/app)
is a complete application in this layout.

(import-direction)=

## Keep imports pointing one way

Within a subject, models import nothing from the other modules, schemas
import models, and views import both. The composition of the application
imports the views. With this order, you can import one subject without the
rest. Alembic needs that, because it loads the models without an
application. Tests need it too, because they load one view without the rest
of the app.

Importing a subject package must not register anything. Keep `__init__.py`
empty, or give it only a docstring. Import the module that holds a class,
not a module that re-exports a whole subject.

(project-base)=
## Declare the base once

Declare your declarative base in `app/models.py`, and import it in the
`models.py` of each subject:

```python
# app/models.py
from sqlalchemy.orm import DeclarativeBase

import fastapi_restly as fr


class Base(fr.ModelDefaults, DeclarativeBase):
    pass
```

```python
# app/users/models.py
from sqlalchemy.orm import Mapped

import fastapi_restly as fr

from ..models import Base


class User(fr.IDMixin, Base):
    email: Mapped[str]
```

Model mixins that several subjects share, such as a tenant column, also go in
`app/models.py`. [Models](#model-defaults) explains what goes in the base.

(project-current)=
## Share Current from the package root

When several modules need values from the request, declare
[Current](context.md) in `app/current.py`. Import it from there
wherever you use it:

```python
from app.current import Current
```

The module can also define `SetCurrentContextDep` with
{meth}`Current.depends <fastapi_restly.clauses.ContextNamespace.depends>`.
Attach this dependency in `main.py` or in the shared `views.py`.

Do not import view modules or the application factory in `current.py`.
Models and scripts must be able to import it without building an application.
`restly new` does not generate this module.

(compose-in-one-place)=

## Compose in one place

Register all your views in one place: a `VIEWS` tuple in `main.py`, which
the application factory registers after it calls
{func}`fr.configure(app, ...) <fastapi_restly.db.configure>`. This is the
`main.py` that `restly new` writes:

```{literalinclude} ../example-projects/starter/app/main.py
:language: python
```

Add each new view to `VIEWS`. Each view imports its own models, so importing
`main.py` imports every model, and `Base.metadata` then holds the whole
schema. Alembic's `env.py` imports `main.py` for this reason; see
[Set up migrations with Alembic](#alembic). A model that no view imports, such as an
outbox table, needs its own import, which that section also explains.

Importing `main.py` must not build the application. `create_app()` builds it.
The server calls it, and the test suite calls it with its own settings.
[Run the app](#running-the-app) shows how the server calls it, and why the
application object does not belong in `main.py`. The lifespan in
this file closes the engine on shutdown; see
[Close the engine on shutdown](#engine-disposal).

(project-views)=

## Share view behavior from the package root

Behavior that every view shares belongs in a root `views.py`: the base class
that your views inherit from, and any mixins. The subject packages hold the
concrete views, and the root module holds what they build on. Add this module
when a second view needs the same behavior. For this reason, a generated
project does not have one.

```python
# app/users/views.py
from ..views import AuthenticatedView, SoftDeleteMixin

from .models import User
from .schemas import UserSchema


class UserView(SoftDeleteMixin, AuthenticatedView):
    prefix = "/users"
    model = User
    schema = UserSchema
```

[Share behavior with base views](#share-behaviour-with-base-views) explains
the base class, and [Where tenant, audit and soft-delete rules go](#structural-pieces) explains the mixins. The
SaaS example has this
[root `views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/views.py).

(when-to-add-modules)=

## Add modules only when you need them

Do not create a module before you have something to put in it. In particular,
a subject does not need a `service.py`. For CRUD, the view class already is
that layer. Your logic goes in business methods such as
{meth}`create <fastapi_restly.views.RestView.create>` and
{meth}`update <fastapi_restly.views.RestView.update>`. A layer that only
passes calls on to them adds code but no new place to change behavior. Write
a `service.py` when logic also runs outside a request, such as work that a
background worker shares; see
[Run the same logic in a script or a worker](#logic-outside-a-view).

The same holds for `dependencies.py`, `constants.py` and `exceptions.py` in a
subject. Add one when it holds more than one item.

When `main.py` grows long, move `VIEWS` and the registration loop to an
`api.py` with a `register_views(app)` function. `main.py` imports it, so
importing `main.py` still imports every model.

Keep concerns of the whole application in top-level modules that are named
for what they do, such as `auth.py`, `settings.py` or `outbox.py`. Turn one
into a package only when it grows several modules that belong together.

(api-versioning)=

## Serve more than one API version

Give each version its own router with its own prefix, and its own tuple of
views. A view that does not change goes in both tuples:

```python
# app/main.py
import fastapi_restly as fr
from fastapi import APIRouter, FastAPI

from .orders.views import OrderV2View, OrderView
from .settings import Settings, get_settings
from .users.views import UserView

VIEWS = {
    "/v1": (UserView, OrderView),
    "/v2": (UserView, OrderV2View),  # OrderV2View has the new contract
}


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = get_settings()
    app = FastAPI()
    fr.configure(app, async_database_url=settings.database_url)
    for prefix, views in VIEWS.items():
        router = APIRouter(prefix=prefix)
        for view in views:
            fr.include_view(router, view)
        app.include_router(router)
    return app
```

A view that you register on two routers serves its routes on both. The
default operation IDs include the full path, so the two versions get
different IDs; see [The names and operation IDs of the CRUD routes](#default-route-metadata).
A base view can also hold the version prefix for all the views that inherit
from it; see [Concatenate URL prefixes](#prefix-concatenation).

## See also

- [Database & Configuration](database.md): the engine, the sessions, and
  `fr.configure()`.
- [Deploying](deploying.md): run the application, and set up Alembic.
- [Testing](testing.md): the `conftest.py` for this layout.
- [Nest a resource under its parent](#nested-resources) and
  [Receive a webhook](#receive-a-webhook): shapes that your existing app
  probably has.
