# Use Restly in an Existing Project

{class}`RestView <fastapi_restly.views.RestView>` provides CRUD endpoints for
a model. Register a view on the same `FastAPI` app or `APIRouter` as your
existing routes. Add one resource at a time. Individual endpoints can remain
plain FastAPI routes.

## Add a RestView Next to Existing Routes

Use {func}`fr.include_view(...) <fastapi_restly.views.include_view>` wherever you already compose routes. Existing routers
and views can share the same parent app or router:

```python
from fastapi import APIRouter, FastAPI
import fastapi_restly as fr

from .db import async_engine          # the async engine your app already builds
from .orders import router as orders_router

app = FastAPI()
fr.configure(app, async_engine=async_engine)

api = APIRouter(prefix="/api")

api.include_router(orders_router, prefix="/orders")  # existing FastAPI routes


class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserSchema


fr.include_view(api, UserView)  # registers /api/users CRUD routes

app.include_router(api)
```

`fr.include_view` works as a direct call (`fr.include_view(api, UserView)`,
above) or as a class decorator (`@fr.include_view(app)`, used later in this
guide); both register the same routes. Pass `app` to `fr.configure`, also
when the views are on a router: then Restly can check the app's OpenAPI spec
for two classes with the same name
([Name your schemas](#name-your-schemas)).

Adoption is per resource. In the example above, orders stay hand-written while
users use `UserView`. Adding a `ProductView` later does not require changing the
orders router.

You can also keep plain FastAPI routes beside a view for endpoints
that are not part of the CRUD surface:

```python
@api.get("/users/{id}/export")
async def export_user(id: int):
    ...
```

## Step Out for One Endpoint

If one default CRUD route should be hand-written, exclude it and
add the FastAPI route yourself:

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

That leaves `UserView` responsible for list, create, read, and update while DELETE
uses your ordinary FastAPI implementation. For smaller changes that keep the
same HTTP contract, prefer overriding the business method ({meth}`get_many <fastapi_restly.views.RestView.get_many>`, {meth}`get_one <fastapi_restly.views.RestView.get_one>`,
{meth}`create <fastapi_restly.views.RestView.create>`, {meth}`update <fastapi_restly.views.RestView.update>`, {meth}`delete <fastapi_restly.views.RestView.delete>`); for a
different status code, response shape, or query interface, see
[Request Lifecycle & Hooks](customize.md).

## Step Out for a Whole Resource

There is no global Restly router to unwind. A resource is included only where
you call {func}`fr.include_view(...) <fastapi_restly.views.include_view>`. To move a resource back to plain FastAPI,
remove that include call and register an `APIRouter` with the same prefix and
path operations.

Your models, schemas, dependencies, and session setup can stay in ordinary app
modules.

## Replace an Existing Hand-Written Router

To replace a hand-written CRUD router with a
{class}`RestView <fastapi_restly.views.RestView>`, map the existing HTTP
contract first:

1. Map your routes to the view's default CRUD contract. `GET /`, `POST /`, and
   `GET`/`PATCH`/`DELETE` on `/{id}` are covered ([the exact
   contract](#default-crud-routes)). Anything else on the
   router (exports, actions) stays as custom routes on the view or as plain
   FastAPI routes beside it.
2. Keep custom semantics out of the swap. A route whose contract differs
   (e.g. `PUT` updates, a non-204 delete) can be excluded via
   {attr}`exclude_routes <fastapi_restly.views.BaseRestView.exclude_routes>` and kept hand-written until you adapt it.
3. Pin the HTTP contract with tests first. Write
   [`RestlyTestClient`](howto_testing.md) tests against the *old* router's
   responses, then swap in the view and run them unchanged; payload or
   status drift shows up immediately.

A resource in mid-migration looks like this:

```python
class ProductView(fr.AsyncRestView):
    prefix = "/products"
    model = Product
    schema = ProductSchema          # match your old response shape exactly
    exclude_routes = (fr.ViewRoute.DELETE,)  # old DELETE returns the object


fr.include_view(api, ProductView)
api.include_router(legacy_delete_router)  # until the contract is adapted
```

## See also

- [Testing](howto_testing.md): pin the
  HTTP contract while migrating; savepoint-isolated tests against your DB.
- [Deploying](deploying.md): engine configuration from environment values,
  Alembic, and a production `main.py`.
- [Nested Resources](#nested-resources) and
  [Receive a webhook](#receive-a-webhook): shapes your existing app probably has.
