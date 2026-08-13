# Use Restly in an Existing Project

FastAPI-Restly registers ordinary FastAPI path operations. You can mount Restly
views on the same `FastAPI` app or `APIRouter` as your existing routes, adopt it
one resource at a time, and move individual endpoints back to plain FastAPI when
that is the clearer shape.

## Add Restly Next to Existing Routes

Use {func}`fr.include_view(...) <fastapi_restly.views.include_view>` wherever you already compose routes. Existing routers
and Restly views can share the same parent app or router:

```python
from fastapi import APIRouter, FastAPI
import fastapi_restly as fr

from .db import async_engine          # the async engine your app already builds
from .orders import router as orders_router

app = FastAPI()
fr.configure(async_engine=async_engine)

api = APIRouter(prefix="/api")

api.include_router(orders_router, prefix="/orders")  # existing FastAPI routes


class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserRead


fr.include_view(api, UserView)  # generated /api/users routes

app.include_router(api)
```

`fr.include_view` works as a direct call (`fr.include_view(api, UserView)`,
above) or as a class decorator (`@fr.include_view(app)`, used later in this
guide); both register the same routes.

Adoption is per resource. In the example above, orders stay hand-written while
users use Restly. Adding a `ProductView` later does not require changing the
orders router.

You can also keep plain FastAPI routes beside a Restly resource for endpoints
that are not part of the CRUD surface:

```python
@api.get("/users/{id}/export")
async def export_user(id: int):
    ...
```

## Step Out for One Endpoint

If one generated endpoint should be hand-written, exclude only that route and
add the FastAPI route yourself:

```python
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserRead
    exclude_routes = (fr.ViewRoute.DELETE,)


fr.include_view(api, UserView)


@api.delete("/users/{id}", status_code=204)
async def delete_user(id: int):
    ...
```

That leaves Restly responsible for list, create, read, and update while DELETE
uses your ordinary FastAPI implementation. For smaller changes that keep the
same HTTP contract, prefer overriding the business method ({meth}`get_many <fastapi_restly.views.RestView.get_many>`, {meth}`get_one <fastapi_restly.views.RestView.get_one>`,
{meth}`create <fastapi_restly.views.RestView.create>`, {meth}`update <fastapi_restly.views.RestView.update>`, {meth}`delete <fastapi_restly.views.RestView.delete>`) or its `handle_<verb>` handler; for a
different status code, response shape, or query interface, see
[Customize RestView](customize.md).

## Step Out for a Whole Resource

There is no global Restly router to unwind. A resource is included only where
you call {func}`fr.include_view(...) <fastapi_restly.views.include_view>`. To move a resource back to plain FastAPI,
remove that include call and register an `APIRouter` with the same prefix and
path operations.

Your models, schemas, dependencies, and session wiring can stay in ordinary app
modules.

## Replace an Existing Hand-Written Router

The previous sections stepped out of Restly; going the other direction,
retiring a hand-written CRUD router in favor of a generated view, is a mapping
exercise:

1. Map your routes to the generated table. `GET /`, `POST /`, and
   `GET`/`PATCH`/`DELETE` on `/{id}` are covered ([the exact
   contract](api_reference.md#generated-rest-endpoints)). Anything else on the
   router (exports, actions) stays as custom routes on the view or as plain
   FastAPI routes beside it.
2. Keep custom semantics out of the swap. A route whose contract differs
   (e.g. `PUT` updates, a non-204 delete) can be excluded via
   {attr}`exclude_routes <fastapi_restly.views.BaseRestView.exclude_routes>` and kept hand-written until you adapt it.
3. Pin the wire contract with tests first. Write
   [`RestlyTestClient`](howto_testing.md) tests against the *old* router's
   responses, then swap in the view and run them unchanged; payload or
   status drift shows up immediately.

A resource in mid-migration looks like this:

```python
class ProductView(fr.AsyncRestView):
    prefix = "/products"
    model = Product
    schema = ProductRead          # match your old response shape exactly
    exclude_routes = (fr.ViewRoute.DELETE,)  # old DELETE returns the object


fr.include_view(api, ProductView)
api.include_router(legacy_delete_router)  # until the contract is adapted
```

## Reuse Your Existing Engine

The most common integration is reusing the engine (or sessionmaker) your app
already builds, with the pool settings and URL handling you trust. Hand exactly
that object to {func}`fr.configure() <fastapi_restly.db.configure>`; Restly does not need to own it:

```python
import fastapi_restly as fr

# the engine your app already creates somewhere central
fr.configure(async_engine=existing_async_engine)

# sync apps: fr.configure(engine=existing_engine)
# or hand over a sessionmaker instead:
#   fr.configure(async_make_session=ExistingAsyncSession)
```

Restly builds its session factory on top and owns the commit on its views;
nothing about the engine changes hands. Reach for a session *generator* (next
section) only when sessions must be constructed in a custom way, such as scoped
sessions, multi-tenant routing, or instrumentation.

## Provide Your Own Session Generator

If your project already manages its own database sessions, configure
FastAPI-Restly to use them instead of its built-in session factory.

If you provide custom sessionmakers or generators, make sure their lifecycle and
session options match the behavior your views rely on. Restly's built-in
factories intentionally use different autoflush defaults for sync and async
sessions and keep `expire_on_commit=False` for both; see
[Session Factory Defaults](technical_details.md#session-factory-defaults).
A custom generator constructs sessions your way but does *not* own the
commit: it should construct, yield, and clean up (close or roll back on the way
out); Restly commits. Customizing how a session is built never takes the commit
away from Restly.

For async views ({class}`AsyncRestView <fastapi_restly.views.AsyncRestView>`), pass an async generator to
{func}`fr.configure() <fastapi_restly.db.configure>`:

```python
from typing import AsyncIterator
from sqlalchemy.ext.asyncio import AsyncSession
import fastapi_restly as fr

async def my_get_db() -> AsyncIterator[AsyncSession]:
    ...
    yield MyAsyncSession()

fr.configure(session_generator=my_get_db)
```

For sync views ({class}`RestView <fastapi_restly.views.RestView>`), pass a sync generator:

```python
from typing import Iterator
from sqlalchemy.orm import Session
import fastapi_restly as fr

def my_get_db() -> Iterator[Session]:
    ...
    yield MySession()

fr.configure(sync_session_generator=my_get_db)
```

The [test fixtures](howto_testing.md#restly_session) clear a configured
generator for the duration of a test, so the request receives the fixture's
isolated session. Configure a sessionmaker (or a database URL) for the tests as
well, and note that whatever the generator body runs per session does not run
there.

## Use a Custom Session Dependency on One View

Use {func}`fr.configure(...) <fastapi_restly.db.configure>` when one session source should be the default for the
application. If only one view should use a different session source, override
the view's `session` dependency instead:

```python
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import fastapi_restly as fr


reporting_engine = create_async_engine("postgresql+asyncpg://user:pass@reports/db")
ReportingSession = async_sessionmaker(
    bind=reporting_engine,
    autoflush=False,
    expire_on_commit=False,
)


async def get_reporting_db() -> AsyncIterator[AsyncSession]:
    # Construct, yield, and clean up; do NOT commit. Restly owns the commit,
    # and the ``async with`` rolls back and closes the session on the way out.
    async with ReportingSession() as session:
        yield session


ReportingSessionDep = Annotated[AsyncSession, Depends(get_reporting_db)]


@fr.include_view(app)
class ReportView(fr.AsyncRestView):
    prefix = "/reports"
    model = Report
    schema = ReportRead
    session: ReportingSessionDep
```

The custom dependency owns session construction and cleanup. Restly still owns
the commit. Use this for read replicas, reporting databases, or other per-view
session wiring.

## Use the Configured Session Off-Request

Outside the request cycle (in background tasks, scripts, or workers), open a
session from FastAPI-Restly's configured factory directly with
{func}`fr.open_async_session() <fastapi_restly.db.open_async_session>` or
{func}`fr.open_session() <fastapi_restly.db.open_session>`:

```python
import fastapi_restly as fr

async with fr.open_async_session() as session:
    result = await session.execute(...)

# sync counterpart:
with fr.open_session() as session:
    result = session.execute(...)
```

Off-request code owns its commit; these helpers do not commit for you.

## Use Your Own DeclarativeBase Models

If your project already has SQLAlchemy models on a custom `DeclarativeBase`,
you can use those models directly in FastAPI-Restly views:

```python
import fastapi_restly as fr
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class AppBase(DeclarativeBase):
    pass


class World(AppBase):
    __tablename__ = "world"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    message: Mapped[str]


@fr.include_view(app)
class WorldView(fr.AsyncRestView):
    prefix = "/world"
    model = World
```

FastAPI-Restly supports these models for generated CRUD routes and
[auto-generated schemas](technical_details.md#auto-generated-schemas). When
creating tables, use your own base metadata (for example
`AppBase.metadata.create_all(...)`).

## See also

- [Test APIs with RestlyTestClient and Fixtures](howto_testing.md): pin the
  wire contract while migrating; savepoint-isolated tests against your DB.
- [Deploying](deploying.md): engine configuration from environment values,
  Alembic, and a production `main.py`.
- [Patterns](patterns.md): the idiomatic answers for nested resources,
  webhooks, and other shapes your existing app probably has.
