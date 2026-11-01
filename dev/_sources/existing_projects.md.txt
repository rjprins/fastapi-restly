# Existing Projects

Restly adds views to a FastAPI application that you already have. It does
not replace the application, and it does not need its own project layout.
Three facts make this work:

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

To start a new project instead, see [New Projects](new_projects.md). That
page also shows the layout that we recommend as an application grows.

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
fr.configure(app, async_session_generator=get_db)

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
A sync app passes its generator as `session_generator`, and its views
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
also with `autoflush=False`, as Restly does:

```python
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
```

Restly reads the response from the object after the commit. With
SQLAlchemy's default, `expire_on_commit=True`, that read fails in an async
view with `MissingGreenlet`, so every `POST` and `PATCH` answers 500. A sync
view still works, but `snapshot(new)` in `after_action_commit` is empty.
Restly warns once for each view, sync or async, that writes through such a
session.

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

## See also

- [Database & Configuration](database.md): the engine, the sessions, and
  `fr.configure()`.
- [Nest a resource under its parent](#nested-resources) and
  [Receive a webhook](#receive-a-webhook): shapes that your existing app
  probably has.
