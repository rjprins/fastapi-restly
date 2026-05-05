# Deploying

This page covers the parts of a production deployment that are specific to
FastAPI-Restly. Everything else — uvicorn workers, gunicorn, TLS, reverse
proxies, Docker, behind-a-proxy headers — is already covered well in
[FastAPI's deployment docs](https://fastapi.tiangolo.com/deployment/) and is
not duplicated here.

## Database configuration

Drive the engine from environment variables. A small `pydantic-settings`
shim keeps the wiring obvious and 12-factor friendly:

```python
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    db_pool_size: int = 5
    db_max_overflow: int = 10


settings = Settings()  # reads DATABASE_URL etc. from the environment
```

Pass those values into `fr.configure()` via an explicit engine so you can
set pool options:

```python
import fastapi_restly as fr
from sqlalchemy.ext.asyncio import create_async_engine

engine = create_async_engine(
    settings.database_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_pre_ping=True,
)

fr.configure(async_engine=engine)
```

`pool_pre_ping=True` is recommended in production: it issues a lightweight
liveness check before handing out a pooled connection, which prevents
stale-connection errors after database restarts or network blips.

## Migrations with Alembic

In production, never call `metadata.create_all()` — use Alembic. Initialise
once in your project root:

```bash
alembic init alembic
```

Point `alembic/env.py` at the metadata of whichever declarative base your
models inherit from (typically `fr.DataclassBase`):

```python
# alembic/env.py
import fastapi_restly as fr
import myapp.models  # noqa: F401 — import side-effect: registers model classes

target_metadata = fr.DataclassBase.metadata
```

Run migrations as part of your release / startup pipeline:

```bash
alembic upgrade head
```

If you want tests to exercise the same migration path, add a project-local
fixture that runs `alembic upgrade head` before the suite. Restly's pytest
plugin does not run migrations automatically. See [How-To: Testing](howto_testing.md).

## A production `main.py` template

```python
from contextlib import asynccontextmanager

import fastapi_restly as fr
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine

from .settings import settings
from .views import UserView


engine = create_async_engine(
    settings.database_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_pre_ping=True,
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    await engine.dispose()


app = FastAPI(lifespan=lifespan)
fr.configure(app, async_engine=engine)
fr.include_view(app, UserView)
```

Notes:

- `fr.configure(app, ...)` installs the default exception handlers
  (currently the `IntegrityError → 409` translator). Pass
  `install_default_exception_handlers=False` to opt out.
- `engine.dispose()` in `lifespan` cleans up the connection pool on
  shutdown so workers exit promptly.
- Register views deliberately during startup with `fr.include_view(app, ViewClass)`;
  avoid relying on import-time side effects in larger apps.

## Running the app

Use any production ASGI runner. The most common options are
[uvicorn](https://www.uvicorn.org/) and
[gunicorn with uvicorn workers](https://www.uvicorn.org/deployment/#gunicorn).
See [FastAPI's deployment docs](https://fastapi.tiangolo.com/deployment/)
for the full picture, including TLS, reverse proxies, and Docker.

A minimal example:

```bash
uvicorn myapp.main:app --host 0.0.0.0 --port 8000 --workers 4
```

Sync `RestView` endpoints run on FastAPI's threadpool, so worker count
still has the usual effect; async `AsyncRestView` endpoints share the
event loop within a worker. Don't use `--reload` in production.
