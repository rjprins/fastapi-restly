# Deploying

A Restly application is a FastAPI application, so
[FastAPI's deployment docs](https://fastapi.tiangolo.com/deployment/) cover
most of a deployment: workers, TLS, reverse proxies and Docker. This page
covers only what Restly adds.

In production, the server calls an application factory, `create_app()`, once
in each worker process. The factory reads the settings, builds the engine,
gives it to {func}`fr.configure() <fastapi_restly.db.configure>`, and
registers the views. Importing a module builds nothing, so Alembic and the
test suite can import your code without a configured environment. In
production, Alembic creates and changes the tables, not the application. Its
`env.py` imports `main.py`, which imports every model.

The sections below follow that order: the settings, the factory, the command
that runs it, the migrations, and a health check. The last section lists the
view settings to check before an API goes public.
[New & Existing Projects](#compose-in-one-place) shows where these files sit
in the project.

(production-settings)=

## Read the database settings from the environment

In production, take the database URL and the pool size from environment
variables. A small `pydantic-settings` class holds them:

```python
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    db_pool_size: int = 5
    db_max_overflow: int = 10


def get_settings() -> Settings:
    """Read the settings from the environment and the .env file."""
    return Settings()  # type: ignore[call-arg]
```

`get_settings()` reads `DATABASE_URL` and the pool fields from the
environment. It fails when `DATABASE_URL` is not set. So call it in the
factory, and not when a module is imported. With `settings = get_settings()`
at the end of this file, every import of your package would need a configured
environment, also in Alembic and in the tests.

Pyright and mypy report `Settings()` as a call with a missing argument. They
do not know that pydantic-settings reads `database_url` from the environment.
The function keeps the `# type: ignore` in one place, so the rest of the app
needs none. The project that `restly new` makes has this function.

Restly does not choose a pool size, so set it yourself. To do that, build the
engine yourself and pass it to `fr.configure()`, as the template below does.
Restly's [engine defaults](#engine-defaults) apply only to an engine that
Restly builds from a URL. For this reason, the template also sets
`pool_pre_ping=True` itself. If your application already builds an engine,
pass that one; see [Reuse your existing engine or sessionmaker](#reuse-existing-engine).

(production-main-template)=

## A production `main.py` template

With these settings, the application factory looks like this:

```python
from contextlib import asynccontextmanager

import fastapi_restly as fr
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine

from .settings import Settings, get_settings
from .tasks.views import TaskView
from .users.views import UserView

VIEWS = (TaskView, UserView)


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = get_settings()
    engine = create_async_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(lifespan=lifespan)
    fr.configure(app, async_engine=engine, health="/health")
    for view in VIEWS:
        fr.include_view(app, view)
    return app
```

Three details matter in production:

- The factory reads the settings and builds the engine only when it runs.
  Without an argument, it reads the settings from the environment. A test
  suite passes its own settings to the same factory, such as
  `create_app(Settings(database_url=...))`; see
  [Test an async-only PostgreSQL app](#async-postgresql-tests).
- The engine belongs to the app that the factory built. The lifespan disposes
  it, so the connections close and the workers exit quickly; see
  [Close the engine on shutdown](#engine-disposal).
- `fr.configure(app, ...)` with `health="/health"` adds the
  [health check](#health-checks).

`VIEWS` is the one place where the application registers its views;
[Compose in one place](#compose-in-one-place) explains it. The SaaS example
builds its application this way
([`app/main.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/main.py)).

(running-the-app)=

## Run the app

Use any production ASGI server. [Uvicorn](https://www.uvicorn.org/) is the
most common one. It can call the factory itself:

```bash
uvicorn "app.main:create_app" --factory --host 0.0.0.0 --port 8000 --workers 4
```

Some platforms and tools need an application object, not a factory. Put that
object in its own module, and leave `main.py` alone:

```python
# app/asgi.py
from .main import create_app

app = create_app()
```

Then `uvicorn app.asgi:app` works, and `main.py` can still be imported
without building anything. The FastAPI CLI cannot call a factory, so name
this module in `pyproject.toml`. Then `fastapi dev` and `fastapi run` work
too:

```toml
[tool.fastapi]
entrypoint = "app.asgi:app"
```

Build the application only once in each process. Restly's database
configuration belongs to the process, so a second call to the factory points
the requests of every earlier app at the new database too; see
[one configuration per process](#one-configuration-per-process).

:::{admonition} Details
:class: details

Do not put `app = create_app()` at the end of `main.py` instead of in
`app/asgi.py`. With required settings, importing `main.py` would then need
`DATABASE_URL` everywhere, also in `conftest.py` and in `alembic/env.py`.
Nothing in the package should import `asgi.py`; only the server does.

Sync {class}`RestView <fastapi_restly.views.RestView>` endpoints run on
FastAPI's thread pool. Async
{class}`AsyncRestView <fastapi_restly.views.AsyncRestView>` endpoints share
the event loop of their worker. In both cases, more workers serve more
requests at the same time. Do not use `--reload` in production.
:::

(alembic)=

## Set up migrations with Alembic

In production, do not call `metadata.create_all()`. Use Alembic. For the
recommended `postgresql+asyncpg` setup, create the Alembic environment once,
with Alembic's async template, in the root of your project:

```bash
alembic init -t async alembic
```

Then point `alembic/env.py` at the metadata of your declarative base, the
`Base` that your models inherit from. The metadata knows only the models that
have been imported. So `env.py` imports `main.py`: `main.py` imports every
view, and each view imports its models. Because the factory builds nothing
at import time, this import costs nothing else:

```python
# alembic/env.py
import app.main  # noqa: F401  (imports every view, and each view its model)
from app.models import Base

target_metadata = Base.metadata
```

A model that no view imports, such as an outbox table or an audit table, is
not in the metadata after that import. Import it at module level in the code
that uses it, not inside a function. If only a worker or a script uses it,
import it in `main.py` next to `VIEWS`. Run `alembic check` in CI: it reports
a missing model as a dropped table, so you see the mistake.

The `env.py` files of the
[starter](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/starter/alembic/env.py)
and the
[SaaS example](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/alembic/env.py)
are complete versions of this setup. They read the database URL from the
application's settings when Alembic gets none.

Run the migrations when you release, or when the application starts:

```bash
alembic upgrade head
```

:::{admonition} Details
:class: details

The generic template that `alembic init alembic` creates builds a sync engine,
and it cannot open an async URL. Use it only when Alembic gets a separate
sync database URL. Alembic's
[asyncio recipe](https://alembic.sqlalchemy.org/en/latest/cookbook.html#using-asyncio-with-alembic)
shows the same template, and how to change an existing environment.

Tests can run the same migrations: pass `alembic_upgrade=True` to
{func}`fr.testing.configure_tests() <fastapi_restly.testing.configure_tests>`;
see [Build the test schema](#test-databases-and-migrations).
:::

(timezone-migration)=

### Give an existing datetime column a time zone

A base with {class}`fr.ModelDefaults <fastapi_restly.models.ModelDefaults>`
maps a plain `Mapped[datetime]` column to `DateTime(timezone=True)`. On
PostgreSQL, you must say how to read the stored values when you change an
existing column without a time zone. If the values are in UTC, use a
migration such as this one, and review it:

```python
import sqlalchemy as sa
from alembic import op

op.alter_column(
    "event",
    "occurred_at",
    type_=sa.DateTime(timezone=True),
    postgresql_using="occurred_at AT TIME ZONE 'UTC'",
)
```

A type change without `postgresql_using` can read the values in the local
time zone of the server. Use `mapped_column(DateTime())` on the model only for
a column that stores a time without a time zone on purpose.

(health-checks)=

## Add a health check

When you name a path in {func}`fr.configure() <fastapi_restly.db.configure>`,
Restly adds an endpoint there:

```python
fr.configure(app, async_engine=engine, health="/health")
```

`GET /health` then answers `200` with `{"status": "ok"}`, and it shows in
`/docs` like any other route. You choose the path; `/healthz` and `/up` are
also common. Without the argument, Restly adds no route. If your application
already has a route at that path, Restly leaves it alone.

This is a liveness check: it shows that the process is up and answers. It
does not query the database. It fits a probe that restarts the container
when the check fails:

```yaml
livenessProbe:
  httpGet:
    path: /health
    port: 8000
```

A readiness probe takes the instance out of service instead of restarting it.
That is the place to check the database and other services. Write it as an
ordinary route, so that you control its checks and its timeouts.

(before-public)=

## Before you expose an API publicly

Restly is made first for APIs with known clients, such as an internal app or
an admin panel, so its limits on the work that one request can cause are
generous by default. A page has at most `max_page_size` rows. A client sorts
and filters only by the keys that the view allows, and uses each sort key only
once. A dotted key joins another table, so dotted keys are off until the view
turns them on. If you find a request that has no such limit, please report it
as a bug.

Access works differently: the rules for what a client may see or change are
just as strict for an internal API as for a public one.

Restly does not limit how many requests a client sends. Put rate limits,
timeouts and a maximum body size in the proxy in front of the app, and a
statement timeout in the database.

For a public API, lower the limits on one request. Start with a table that
only grows, such as an audit log, events or imports. It gets large without
anyone noticing, and clients want to filter and sort it. Check these settings
on its view:

- Lower the page size and its maximum with
  {class}`fr.NumberedPagination <fastapi_restly.views.NumberedPagination>`,
  such as `default_page_size=25` and `max_page_size=100`. `max_page` limits
  how far a client can page. See [Page through a list](#list-pagination).
- Turn on only the filters and sort keys that clients need, with
  `include_filters` and `include_sort_keys`. A filter or a sort on a column
  without an index reads the whole table. See
  [Choose the keys of a list](#choose-list-keys).
- Keep `unknown_query_params = "forbid"`, the default. A mistyped filter is
  then a `422`, not a list with more rows than the client asked for. See
  [Accept extra query parameters](#extra-query-parameters).
- Put the rule for which rows a caller may see in a scope, not in code that
  filters the rows after the read. A scope runs in SQL, so the rows of a
  list, its count and its pages agree. See
  [Give one view other rows](#view-scope).

## See also

- [Database & Configuration](database.md): engines, sessions and
  `fr.configure()`.
- [New & Existing Projects](projects.md): the project layout,
  and Restly next to an existing app.
- The [SaaS example](https://github.com/rjprins/fastapi-restly/tree/main/example-projects/saas):
  an example in production shape. It has PostgreSQL services in Compose,
  validated settings, an async engine that the application owns, migrations,
  and tests that run the migrations.
