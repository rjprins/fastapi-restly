# Using FastAPI-Ding in an Existing Project

## Session Management

If you already have a session generator, you can configure FastAPI-Ding to use it:

```python
from typing import AsyncIterator
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi_ding import settings

async def my_get_db() -> AsyncIterator[AsyncSession]:
    ...
    yield MyAsyncSession()

settings.session_generator = my_get_db()
```

The other way around, you can use FastAPI-Ding's session generator like this:

```python
from fastapi_ding import get_session

with get_session() as session:
    session.execute(...)
```

## Let fa use your DeclarativeBase class

If you already have a DeclarativeBase class, you can make FastAPI-Ding use it:

```python
from fastapi_ding import DingBase, AsyncAlchemyView
from sqlalchemy import Mapped

class World(DingBase):
    message: Mapped[str]

class WorldView(AsyncAlchemyView):
    prefix = "world"
    model = World
    schema = WorldSchema
```
