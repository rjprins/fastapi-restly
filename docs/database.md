# Database & Configuration


Restly exposes one public process-wide runtime configuration. Most applications
configure it once during startup:

```python
fr.configure(async_database_url="sqlite+aiosqlite:///app.db")
```

{func}`fr.configure(...) <fastapi_restly.db.configure>` rejects no-op calls; pass at least one setup option. Its
reference entry lists every accepted option. This page does not repeat them.

Internally, Restly keeps a private context object so its own tests and fixtures
can isolate runtime state. That context is not a public multi-engine feature.
If an application needs multiple databases, use a custom FastAPI dependency or
session generator for that view. Restly does not currently bind different views
to different named contexts.

(reuse-existing-engine)=

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

{func}`fr.configure() <fastapi_restly.db.configure>` builds the session
factory on top. {class}`RestView <fastapi_restly.views.RestView>` commits
its write actions. Your application still controls the engine. Use a session
*generator* (next section) only when sessions must be constructed in a custom
way, such as scoped sessions, multi-tenant routing, or instrumentation.

The SaaS example builds its own async engine from its settings and hands it to
`fr.configure` ([`app/main.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/main.py)).

(own-session-generator)=

## Provide Your Own Session Generator

If your project already manages its own database sessions, configure
FastAPI-Restly to use them instead of its built-in session factory.

If you provide custom sessionmakers or generators, make sure their lifecycle and
session options match the behavior your views rely on. Restly's built-in
factories intentionally use different autoflush defaults for sync and async
sessions and keep `expire_on_commit=False` for both; see
[Session Factory Defaults](#session-factory-defaults).
A custom generator constructs sessions your way but does *not* own the
commit. It should construct, yield, and clean up (close or roll back on the way
out). {class}`RestView <fastapi_restly.views.RestView>` commits its write actions.

For async views ({class}`AsyncRestView <fastapi_restly.views.AsyncRestView>`), pass an async generator to
{func}`fr.configure() <fastapi_restly.db.configure>`:

```python
from typing import AsyncIterator
from sqlalchemy.ext.asyncio import AsyncSession
import fastapi_restly as fr

async def my_get_db() -> AsyncIterator[AsyncSession]:
    async with MyAsyncSession() as session:
        yield session

# Before registering routes or including views.
fr.configure(session_generator=my_get_db)
```

For sync views ({class}`RestView <fastapi_restly.views.RestView>`), pass a sync generator:

```python
from typing import Iterator
from sqlalchemy.orm import Session
import fastapi_restly as fr

def my_get_db() -> Iterator[Session]:
    with MySession() as session:
        yield session

# Before registering routes or including views.
fr.configure(sync_session_generator=my_get_db)
```

Restly resolves the configured generator through FastAPI's dependency graph.
An existing `Depends(my_get_db)` in an authentication dependency or route and
the view's `self.session` receive the same session within a request. That means
a user loaded by authentication can be assigned directly to a relationship in
the view. The generator runs once, with one cleanup, under FastAPI's normal
dependency caching. Custom `use_cache=False`, dependency scopes, and security
scopes follow FastAPI's own caching rules.

Configure the generator before registering any route that uses
{data}`fr.SessionDep <fastapi_restly.db.SessionDep>` or
{data}`fr.AsyncSessionDep <fastapi_restly.db.AsyncSessionDep>`, including plain
FastAPI routes. FastAPI fixes the
dependency graph at registration, so setting or replacing that generator later
raises {class}`RestlyConfigurationError <fastapi_restly.exc.RestlyConfigurationError>`.
An explicit `session: Annotated[..., Depends(reporting_get_db)]` on a view keeps
using `reporting_get_db`.

Sharing also works when Restly owns session creation. Use its public aliases in
your application's dependencies and plain routes. This example assumes your
application supplies `User` and `current_user_id()`:

```python
fr.configure(async_engine=existing_async_engine)

async def get_current_user(session: fr.AsyncSessionDep):
    return await session.get(User, current_user_id())
```

Views and application dependencies using `fr.AsyncSessionDep` share one session.
Use `fr.SessionDep` for sync code. Both aliases keep Restly's uncommitted-changes
warning, including when used on plain routes.

The [test fixtures](howto_testing.md#restly_session) override the configured
generator through `app.dependency_overrides` under rollback isolation. Native
application dependencies and Restly then share an isolated request session.
The generator body does not run for requests during those tests. If no factory
is configured, setup opens the generator to discover its session's bind.
Generators needing request arguments require an explicit test engine or
sessionmaker as well. For your own overrides, use
`app.dependency_overrides[my_get_db] = test_get_db`.

(session-dependency-one-view)=

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
    # Construct, yield, and clean up. RestView owns the commit.
    # The context manager rolls back and closes the session on the way out.
    async with ReportingSession() as session:
        yield session


ReportingSessionDep = Annotated[AsyncSession, Depends(get_reporting_db)]


@fr.include_view(app)
class ReportView(fr.AsyncRestView):
    prefix = "/reports"
    model = Report
    schema = ReportSchema
    session: ReportingSessionDep
```

The custom dependency owns session construction and cleanup.
{class}`RestView <fastapi_restly.views.RestView>` still owns the commit.
Use this for read replicas, reporting databases, or any other view that needs its own session.

`AsyncSessionDep` and `SessionDep` wrap a session source as a FastAPI
sub-dependency. The source is either Restly's built-in generator or the custom
generator passed to {func}`configure() <fastapi_restly.db.configure>` before
route registration. Making that source a dependency lets FastAPI share its
cached session with the application's `Depends(get_db)`, without rewriting
view annotations. The wrapper checks for uncommitted changes before the source
cleans up. The built-in generators roll back and close on exit.

(session-off-request)=

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

(engine-disposal)=

## Engine Disposal

Disposing an engine closes the connections its pool is holding. Restly never
disposes an engine, including the ones it builds, because an engine is meant to
live as long as the process that serves requests through it. When that process
exits, the operating system reclaims the sockets, so most applications never
need to dispose anything.

Asynchronous applications are the exception worth handling. `AsyncEngine.dispose()`
is a coroutine, and nothing awaits it during interpreter shutdown, so an async
engine that is never disposed drops its connections rather than closing them.
The database server is not what notices. The operating system closes the sockets
as the process exits, and a backend waiting for its next command treats that as
an ordinary end of session. The cost lands in your own shutdown output: a
`ResourceWarning` for every connection left in the pool, and, when a request is
still in flight, a `RuntimeError: Event loop is closed` traceback from the
finalizer trying to terminate a connection on a loop that is already gone.
SQLite has no server and no socket, so none of this applies to it.

An application that builds its own engine already holds it and disposes it in
its lifespan, which is what [the production
template](#production-main-template) does. When you configure from a
URL, Restly owns the engine, and
{func}`fr.db.get_async_engine() <fastapi_restly.db.get_async_engine>` hands it
back:

```python
from contextlib import asynccontextmanager

import fastapi_restly as fr
from fastapi import FastAPI


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await fr.db.get_async_engine().dispose()


app = FastAPI(lifespan=lifespan)
fr.configure(app, async_database_url="postgresql+asyncpg://localhost/app")
```

Synchronous applications call
{func}`fr.db.get_engine() <fastapi_restly.db.get_engine>` and `dispose()`
without awaiting. Note that Starlette runs `on_startup` and `on_shutdown`
handlers only when the application was built without a `lifespan` argument, so
disposal belongs in the lifespan itself rather than in a handler appended to
`app.router`.

An application that disposes its engine in the lifespan cannot be tested against
an in-memory SQLite database. Restly's test clients run the ASGI lifespan, so the
disposal happens at the end of every test. An in-memory database lives inside its
connection, so closing it discards the schema and the next test finds no tables.
Point the suite at a file database instead.

Reconfiguring Restly in a running process abandons the previous pool rather than
closing it. CPython reclaims it once the garbage collector reaches the cycle the
engine sits in, which is why a test suite that reconfigures per test can hold
several pools open at once. Suites that care dispose explicitly; Restly's own
fixtures do.

(sqlite-wal)=

## Turn on WAL for SQLite

`PRAGMA journal_mode=WAL` is the notable omission. WAL lets readers run while a
writer holds the database, so it is the usual answer to `database is locked` on
a file-backed SQLite database serving concurrent requests. Restly does not set
it, because journal mode is written into the database file and outlives every
process that touches it: a default here would permanently change a database you
own, and every later reader would inherit the choice. It is also a loud failure
rather than a silent one, unlike the pragmas above. Turn it on yourself when you
want it:

```python
from sqlalchemy import create_engine, event

engine = create_engine("sqlite:///app.db")


@event.listens_for(engine, "connect")
def _enable_wal(dbapi_connection, connection_record):
    dbapi_connection.execute("PRAGMA journal_mode=WAL")


fr.configure(engine=engine)
```

Note that WAL adds `-wal` and `-shm` files beside the database, which are
removed on clean shutdown but remain after a hard kill, and that it does not
work over network filesystems.


## Internals

(engine-defaults)=

### Engine Defaults

{func}`fr.configure() <fastapi_restly.db.configure>` builds an engine only when
you give it a URL. To that engine, and no other, Restly applies the options a
web application almost always wants but SQLAlchemy leaves at its library
defaults:

| Backend | Applied |
|---|---|
| SQLite, in-memory | `poolclass=StaticPool`, `connect_args={"check_same_thread": False}` |
| SQLite, any | `PRAGMA foreign_keys=ON`, on every connection |
| PostgreSQL | `pool_pre_ping=True`, `pool_recycle=1800` |

An in-memory SQLite database lives inside its connection, so the default pool,
which opens one connection per thread, serves every thread but the first a
second and empty database. FastAPI runs `def` endpoints on a thread pool, so
`StaticPool` is what makes an in-memory URL behave as one database.

SQLite enforces foreign keys only under `PRAGMA foreign_keys=ON`. Without it the
`ForeignKey` constraints your models declare are parsed and then ignored, so
`ondelete` never fires and a dangling reference is stored instead of raising the
`IntegrityError` that becomes a 409.

On PostgreSQL, `pool_pre_ping=True` tests a pooled connection before handing it
out, turning a connection left stale by a database restart or a network blip
into a reconnect rather than an error. `pool_recycle` then discards connections
older than 30 minutes, because proxies such as pgbouncer drop idle connections
without telling the client. Pool sizing stays yours: Restly sets neither
`pool_size` nor `max_overflow`.

Each of these is connection state, and that is the line they stop at. Restly
configures the connections it opens and does not modify the database they reach,
so nothing here leaves a mark on your data once the process exits.

All of this rides on the URL rung alone. Pass `engine=`, `make_session=` or a
session generator and Restly leaves your object exactly as it arrived, so
building the engine yourself is how you decline any of these defaults.

(session-factory-defaults)=

### Session Factory Defaults

When {func}`fr.configure() <fastapi_restly.db.configure>` creates session factories from URLs or engines, Restly
sets a few SQLAlchemy session options intentionally:

| Factory | Autoflush | Expire on commit |
|---|---|---|
| Async `async_sessionmaker` | `False` | `False` |
| Sync `sessionmaker` | SQLAlchemy default (`True`) | `False` |

`expire_on_commit=False` is used for both sync and async sessions so ORM
objects remain readable after a route commits. Restly's write handlers commit
inside the request, and the response-schema conversion reads attributes from the
committed object afterwards (as does an `after_action_commit` hook that inspects it).
With `expire_on_commit=True` the commit expires those attributes, so each such
read becomes an implicit database read: in async code it raises
`MissingGreenlet`, because the serializer -- and any such hook -- runs in plain
async context; in sync code it quietly makes response rendering database-dependent.

The autoflush setting is intentionally different. Async sessions disable
autoflush because autoflush can turn a read operation into an implicit write and
database I/O must happen at explicit awaited SQLAlchemy boundaries. Restly's
async CRUD helpers flush explicitly when writes should hit the database. Sync
sessions keep SQLAlchemy's default autoflush behavior, preserving the usual
unit-of-work ergonomics where ORM queries see pending in-session changes.

Projects with custom sessionmakers or generators should preserve these defaults
unless they need different behavior.
