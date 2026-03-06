# Using FastAPI-Restly in an Existing Project

## Session Management

If you already have a session generator, you can configure FastAPI-Restly to use it:

```python
from typing import AsyncIterator
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi_restly import settings

async def my_get_db() -> AsyncIterator[AsyncSession]:
    ...
    yield MyAsyncSession()

settings.session_generator = my_get_db
```

Or use FastAPI-Restly's configured session proxy:

```python
import fastapi_restly as fr

async with fr.FRAsyncSession() as session:
    session.execute(...)
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


class WorldView(fr.AsyncAlchemyView):
    prefix = "world"
    model = World
```

FastAPI-Restly supports these models for generated CRUD routes and auto-generated schemas.
When creating tables, use your base metadata (for example `AppBase.metadata.create_all(...)`).

## Isolating Runtime State Per App

FastAPI-Restly keeps default runtime state for convenience, but you can isolate
state per context when running multiple apps in one process:

```python
from fastapi_restly.db import FRGlobals, use_fr_globals

app_a_globals = FRGlobals()
app_b_globals = FRGlobals()

with use_fr_globals(app_a_globals):
    ...

with use_fr_globals(app_b_globals):
    ...
```
