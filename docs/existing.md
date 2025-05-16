# Using FastAPI-Alchemy in an Existing Project

`fa` can be used in any existing FastAPI project that also uses SQLAlchemy. Other ORMs are not supported.

## Let `fa` Use Your SQLAlchemy Session

If you already have code that creates SQLAlchemy `AsyncSession` objects, you can tell `fa` to use it.

```python
from typing import AsyncIterator
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi_alchemy import settings

async def my_get_db() -> AsyncIterator[AsyncSession]:
    ...
    yield MyAsyncSession()

settings.session_generator = my_get_db()
```

The other way around is of course also possible. So if you have need for a database session outside of `fa`, import the session generator like this:
```python
from fastapi_alchemy import get_session

with get_session() as session:
    session.execute(...)
```

## Let `fa` Use Your DeclarativeBase Class
Wait, does `fa` even need any specific base class??
