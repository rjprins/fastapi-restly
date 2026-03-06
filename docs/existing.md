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

## Let fr use your DeclarativeBase class

If you already have a DeclarativeBase class, you can make FastAPI-Restly use it:

```python
import fastapi_restly as fr
from sqlalchemy import Mapped

class World(fr.Base):
    message: Mapped[str]

class WorldView(fr.AsyncAlchemyView):
    prefix = "world"
    model = World
    schema = WorldSchema
```
