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

settings.session_generator = my_get_db()
```

The other way around, you can use FastAPI-Restly's session generator like this:

```python
from fastapi_restly import get_session

with get_session() as session:
    session.execute(...)
```

## Let fr use your DeclarativeBase class

If you already have a DeclarativeBase class, you can make FastAPI-Restly use it:

```python
from fastapi_restly import RestlyBase, AsyncAlchemyView
from sqlalchemy import Mapped

class World(RestlyBase):
    message: Mapped[str]

class WorldView(AsyncAlchemyView):
    prefix = "world"
    model = World
    schema = WorldSchema
```
