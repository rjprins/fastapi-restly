# How-To: Use FastAPI-Restly in an Existing Project

FastAPI-Restly registers ordinary FastAPI path operations. You can mount Restly
views on the same `FastAPI` app or `APIRouter` as your existing routes, adopt it
one resource at a time, and move individual endpoints back to plain FastAPI when
that is the clearer shape.

## Add Restly Next to Existing Routes

Use `fr.include_view(...)` wherever you already compose routes. Existing routers
and Restly views can share the same parent app or router:

```python
from fastapi import APIRouter, FastAPI
import fastapi_restly as fr

from .orders import router as orders_router

app = FastAPI()
api = APIRouter(prefix="/api")

api.include_router(orders_router, prefix="/orders")  # existing FastAPI routes


class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserRead


fr.include_view(api, UserView)  # generated /api/users routes

app.include_router(api)
```

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
same HTTP contract, prefer overriding the business verb (`get_many`, `get_one`,
`create`, `update`, `delete`) or its `handle_<verb>` request handler; for a
different status code, response shape, or query interface, see
[Override CRUD Behavior and Add Custom Endpoints](howto_override_endpoints.md).

## Step Out for a Whole Resource

There is no global Restly router to unwind. A resource is included only where
you call `fr.include_view(...)`. To move a resource back to plain FastAPI,
remove that include call and register an `APIRouter` with the same prefix and
path operations.

Your models, Pydantic schemas, dependencies, and database session wiring can
stay in ordinary application modules. Restly does not need to own the whole app.

## Provide Your Own Session Generator

If your project already manages its own database sessions, configure
FastAPI-Restly to use them instead of its built-in session factory.

If you provide custom sessionmakers or generators, make sure their lifecycle and
session options match the behavior your views rely on. Restly's built-in
factories intentionally use different autoflush defaults for sync and async
sessions and keep `expire_on_commit=False` for both; see
[Session Factory Defaults](technical_details.md#session-factory-defaults).
Custom generators also own transaction handling: Restly does not add its
`commit_session_on_response` behavior around them.

For async views (`AsyncRestView`), pass an async generator to
`fr.configure()`:

```python
from typing import AsyncIterator
from sqlalchemy.ext.asyncio import AsyncSession
import fastapi_restly as fr

async def my_get_db() -> AsyncIterator[AsyncSession]:
    ...
    yield MyAsyncSession()

fr.configure(session_generator=my_get_db)
```

For sync views (`RestView`), pass a sync generator:

```python
from typing import Iterator
from sqlalchemy.orm import Session
import fastapi_restly as fr

def my_get_db() -> Iterator[Session]:
    ...
    yield MySession()

fr.configure(sync_session_generator=my_get_db)
```

## Use a Custom Session Dependency on One View

Use `fr.configure(...)` when one session source should be the default for the
application. If only one view should use a different session source, override
the view's `session` dependency instead.

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
    async with ReportingSession() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


ReportingSessionDep = Annotated[AsyncSession, Depends(get_reporting_db)]


@fr.include_view(app)
class ReportView(fr.AsyncRestView):
    prefix = "/reports"
    model = Report
    schema = ReportRead
    session: ReportingSessionDep
```

The custom dependency owns its own lifecycle, including commit/rollback policy.
This is the recommended escape hatch for read replicas, reporting databases, or
other per-view session wiring. Restly does not currently provide named engines
or named Restly contexts.

You can also use FastAPI-Restly's configured session proxy directly in your
own code (for example in background tasks):

```python
import fastapi_restly as fr

async with fr.open_async_session() as session:
    result = await session.execute(...)
```

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
auto-generated schemas. When creating tables, use your own base metadata
(for example `AppBase.metadata.create_all(...)`).
