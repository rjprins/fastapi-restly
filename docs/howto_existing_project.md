# How-To: Use FastAPI-Restly in an Existing Project

## Provide Your Own Session Generator

If your project already manages its own database sessions, configure
FastAPI-Restly to use them instead of its built-in session factory.

If you provide custom sessionmakers or generators, make sure their lifecycle and
session options match the behavior your views rely on. Restly's built-in
factories intentionally use different autoflush defaults for sync and async
sessions and keep `expire_on_commit=False` for both; see
[Session Factory Defaults](technical_details.md#session-factory-defaults).

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

You can also use FastAPI-Restly's configured session proxy directly in your
own code (for example in background tasks):

```python
import fastapi_restly as fr

async with fr.async_open_session() as session:
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

## Isolating Runtime State Per App

FastAPI-Restly keeps default runtime state (session factories, database URLs)
in a module-level `FRGlobals` instance. When running multiple apps in the same
process you can isolate state per context:

```python
from fastapi_restly.db import FRGlobals, use_fr_globals

app_a_globals = FRGlobals()
app_b_globals = FRGlobals()

with use_fr_globals(app_a_globals):
    fr.configure(async_database_url="postgresql+asyncpg://host-a/db")
    ...

with use_fr_globals(app_b_globals):
    fr.configure(async_database_url="postgresql+asyncpg://host-b/db")
    ...
```

`use_fr_globals` uses a `ContextVar` internally, so concurrent async tasks
each see the globals object that was active when they started.
