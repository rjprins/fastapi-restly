(configure-restly)=

# Database & Configuration

Every Restly view needs a database session. You tell Restly once, at
startup, where its sessions come from, and every view uses that source. Call
{func}`fr.configure() <fastapi_restly.db.configure>` before you register your
views:

```python
import fastapi_restly as fr
from fastapi import FastAPI

app = FastAPI()
fr.configure(app, async_database_url="sqlite+aiosqlite:///app.db")
```

You can give Restly one of four sources. Each has an async and a sync
argument:

| You pass | Async argument | Sync argument | What Restly does with it |
|---|---|---|---|
| A URL | `async_database_url` | `database_url` | Builds the engine and a sessionmaker |
| An engine | `async_engine` | `engine` | Builds a sessionmaker on your engine |
| A sessionmaker | `async_make_session` | `make_session` | Uses it as it is |
| A session generator | `session_generator` | `sync_session_generator` | Calls it for each request |

Pass a URL when Restly may own the engine. Pass your engine or your
sessionmaker when your application already builds one. Pass a generator when
your routes already get their session from one, such as `get_db`, or when
sessions must be built in a special way. An application that has both
async and sync views passes one async and one sync source.

For each request, a view gets a session from that source. An
{class}`AsyncRestView <fastapi_restly.views.AsyncRestView>` receives it
through {data}`fr.AsyncSessionDep <fastapi_restly.db.AsyncSessionDep>`, and
a {class}`RestView <fastapi_restly.views.RestView>` through
{data}`fr.SessionDep <fastapi_restly.db.SessionDep>`. The source opens the
session and closes it after the request, but it does not commit; see
[What commits, and when](#what-commits).

(one-configuration-per-process)=
There is one configuration for each process. Restly stores it in the process,
not on the app. A second call to `fr.configure()` replaces the sources it
names, and every app in the process then uses the new ones. Restly has no
named configurations for different views. To give one view a different
database, [change the session on that view](#session-dependency-one-view).

When you pass `app`, `fr.configure()` also prepares that app. It checks the
OpenAPI schema for
[two classes with the same name](#name-your-schemas), and with
`health="/health"` it adds a [health check route](#health-checks). The
{func}`reference entry <fastapi_restly.db.configure>` lists every argument.

(reuse-existing-engine)=

## Reuse your existing engine or sessionmaker

Your application may already build an engine, with pool settings that you
trust. Give exactly that object to `fr.configure()`. Restly does not need to
own it:

```python
import fastapi_restly as fr

# The engine that your app already creates
fr.configure(async_engine=existing_async_engine)

# For sync views:
#   fr.configure(engine=existing_engine)
# Or pass your sessionmaker instead:
#   fr.configure(async_make_session=ExistingAsyncSession)
```

With an engine, Restly builds a sessionmaker on top of it. With a
sessionmaker, Restly uses it as it is. Either way, your application
still controls the engine, and Restly applies none of its
[engine defaults](#engine-defaults) to it.

If you pass an async sessionmaker, create it with `expire_on_commit=False`.

:::{admonition} Details
:class: details

Restly reads the response from the object after the commit, and in async code
an expired attribute raises `MissingGreenlet`. Restly warns once for each view
that writes through a session with `expire_on_commit=True`, from any source of
sessions.
[Session factory defaults](#session-factory-defaults) explains the options
that Restly sets on its own factories.

The SaaS example builds its own async engine from its settings and passes it
to `fr.configure()`
([`app/main.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/main.py)).
:::

(own-session-generator)=

## Build sessions with your own generator

Use a session generator when your application already has one, like the
`get_db` that your routes use. Use one also when your sessions need special
setup, such as scoped sessions, routing to a database per tenant, or
instrumentation. The generator builds a session, yields it, and cleans it up.
It must not commit: the view commits its write actions.

For async views, pass an async generator:

```python
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

import fastapi_restly as fr


async def my_get_db() -> AsyncIterator[AsyncSession]:
    async with MyAsyncSession() as session:
        yield session


# Before you register views or routes.
fr.configure(session_generator=my_get_db)
```

For sync views, pass a sync generator:

```python
from collections.abc import Iterator

from sqlalchemy.orm import Session

import fastapi_restly as fr


def my_get_db() -> Iterator[Session]:
    with MySession() as session:
        yield session


# Before you register views or routes.
fr.configure(sync_session_generator=my_get_db)
```

Many `get_db` generators commit after the `yield`. Such a commit saves
changes that should be rolled back: changes that no write action committed,
such as those of a route that forgot to commit. Restly's warning says that
these changes are rolled back, but a generator that commits saves them. So
remove the commit from the generator. The views do not need it.

:::{warning}
Before you remove the commit, add a commit to each of your own routes that
writes. A route that still relies on the commit in the generator answers 200,
but its writes are lost, and nothing warns you. Restly's warning about
changes that were not committed comes only from views, and from routes that
get their session through `fr.AsyncSessionDep` or `fr.SessionDep`.
:::

Give your sessions the same options as Restly's own factories:
`expire_on_commit=False`, and for async sessions also `autoflush=False`. An
async view needs `expire_on_commit=False`. Without it, its `POST` and `PATCH`
routes fail with `MissingGreenlet`. Restly warns once for each view that
writes through such a session;
[Session factory defaults](#session-factory-defaults) explains why.

To use the generator outside a request, with
[`fr.open_session()`](#session-off-request), it must take no arguments. In
tests, Restly's fixtures can replace the generator;
[the `restly_session` fixture](#restly-session-fixture) describes when.

:::{admonition} Details
:class: details

Configure the generator before you register any route that uses
`fr.AsyncSessionDep` or `fr.SessionDep`. This includes plain FastAPI routes.
FastAPI fixes the dependencies of a route when you register it. For this
reason, a call that sets or replaces the generator after that raises
{class}`RestlyConfigurationError <fastapi_restly.exc.RestlyConfigurationError>`.
:::

(share-request-session)=

## Use the view's session in your own dependencies

A view and your own dependencies can share one session in a request. So when
your authentication dependency loads a user, the view can assign that user
object to a relationship directly.

When Restly builds the sessions, use its aliases in your dependencies and
plain routes. This example assumes that your application supplies `User` and
`current_user_id()`:

```python
fr.configure(async_engine=existing_async_engine)


async def get_current_user(session: fr.AsyncSessionDep):
    return await session.get(User, current_user_id())
```

The views and every dependency that uses `fr.AsyncSessionDep` get the same
session within a request. Use `fr.SessionDep` for sync code.

:::{admonition} Details
:class: details

On a plain route, both aliases also warn when the request ends with changes
that were not committed, as they do on a view.

When you configure your own generator, your existing `Depends(my_get_db)`
gives the same session as the view's `self.session`. FastAPI runs the
generator once per request, and cleans it up once. FastAPI's own caching rules
still apply: a dependency with `use_cache=False` gets its own session. For the
same reason, an override of `my_get_db` in `app.dependency_overrides` also
gives the views their session, so a test setup that overrides it keeps
working.
:::

(session-dependency-one-view)=

## Use another database on one view

`fr.configure()` sets the session source for the whole application. To give
one view a different source, such as a read replica or a reporting database,
override the view's `session` dependency:

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
    # Build, yield, and clean up. The view owns the commit.
    # The context manager rolls back and closes the session at the end.
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

Your dependency builds and cleans up the session. The view still commits its
write actions. A generator passed to `fr.configure()` does not change this
view: it keeps `get_reporting_db`.

(session-off-request)=

## Open a session outside a request

Background tasks, scripts and workers run outside a request, so no view gives
them a session. Open one from Restly's configured source with
{func}`fr.open_async_session() <fastapi_restly.db.open_async_session>` or
{func}`fr.open_session() <fastapi_restly.db.open_session>`:

```python
import fastapi_restly as fr

async with fr.open_async_session() as session:
    result = await session.execute(...)

# The sync version:
with fr.open_session() as session:
    result = session.execute(...)
```

These helpers do not commit. Code outside a request commits its own changes.
[Run the same logic in a script or a worker](#logic-outside-a-view) shows how
to share logic between a view and such code.

(engine-disposal)=

## Close the engine on shutdown

Disposing an engine closes the connections in its pool. Restly never disposes
an engine, not even one that it built. An engine normally lives as long as the
process. When the process exits, the operating system closes the connections,
so most sync applications need to do nothing.

Async applications should dispose the engine. `AsyncEngine.dispose()` is a
coroutine, and nothing awaits it when Python shuts down. The database server
does not mind. But your shutdown output shows a `ResourceWarning` for each
connection in the pool. If a request is still running, it also shows a
`RuntimeError: Event loop is closed` traceback. SQLite has no server and no
sockets, so this does not apply to it.

If your application builds its own engine, dispose it in the app's lifespan,
as [the production template](#production-main-template) does. If you
configure from a URL, Restly owns the engine, and
{func}`fr.db.get_async_engine() <fastapi_restly.db.get_async_engine>` returns
it:

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

Sync applications call
{func}`fr.db.get_engine() <fastapi_restly.db.get_engine>` and `dispose()`,
without `await`. Put the call in the lifespan, not in an `on_shutdown`
handler: Starlette runs those handlers only for an app built without a
`lifespan` argument.

An application that disposes its engine in the lifespan cannot use an
in-memory SQLite database in its tests. Restly's test clients run the
lifespan, so the engine is disposed at the end of every test. An in-memory
database lives inside its connection, so closing the connection deletes the
tables, and the next test finds none. Use a file database for the tests.

A second `fr.configure()` call does not close the pool of the engine it
replaces. Python frees that pool later, when the garbage collector runs. So a
test suite that configures again for each test can have several pools open at
the same time. If that matters to you, dispose the old engine yourself.

(sqlite-wal)=

## Turn on WAL for SQLite

A file-based SQLite database that serves several requests at the same time can
fail with `database is locked`. WAL mode fixes this in most cases: readers can
then run while a writer holds the database. Restly does not turn WAL on. The
journal mode is stored in the database file. So the change would stay, also
for every program that opens the file later. Turn it on yourself:

```python
from sqlalchemy import create_engine, event

import fastapi_restly as fr

engine = create_engine("sqlite:///app.db")


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record):
    dbapi_connection.execute("PRAGMA journal_mode=WAL")
    dbapi_connection.execute("PRAGMA foreign_keys=ON")


fr.configure(engine=engine)
```

Because you build the engine here, Restly does not add its
[SQLite defaults](#engine-defaults) to it. The example sets
`PRAGMA foreign_keys=ON` itself for that reason.

WAL adds `-wal` and `-shm` files next to the database. A clean shutdown
removes them, but they stay after the process is killed. WAL does not work on
a network file system.

## Internals

(engine-defaults)=

### Engine defaults

{func}`fr.configure() <fastapi_restly.db.configure>` builds an engine only
when you give it a URL. Restly applies these options to that engine, and to no
other engine. A web application almost always wants them, but SQLAlchemy does
not set them by default:

| Backend | Applied |
|---|---|
| SQLite, in memory | `poolclass=StaticPool`, `connect_args={"check_same_thread": False}` |
| SQLite, any | `PRAGMA foreign_keys=ON`, on every connection |
| PostgreSQL | `pool_pre_ping=True`, `pool_recycle=1800`, and each session in UTC |

An in-memory SQLite database lives inside its connection. The default pool
opens one connection per thread, so each thread after the first gets a second,
empty database. FastAPI runs `def` endpoints on a thread pool. `StaticPool`
keeps every thread on one connection, so an in-memory URL acts as one
database.

SQLite checks foreign keys only with `PRAGMA foreign_keys=ON`. Without it,
SQLite ignores the `ForeignKey` constraints of your models: `ondelete` never
runs, and a reference to a missing row is stored instead of raising the
`IntegrityError` that becomes a 409.

On PostgreSQL, `pool_pre_ping=True` tests a connection from the pool before
the pool hands it out. A connection that a database restart or a network problem
broke then leads to a new connection instead of an error. `pool_recycle`
replaces connections older than 30 minutes, because proxies such as pgbouncer
close idle connections without telling the client. Restly does not set the
pool size: it sets neither `pool_size` nor `max_overflow`.

Each new PostgreSQL session is in UTC, as in Django and Rails. Restly reads
the time zone that the server reports for the session, and sends
`SET TIME ZONE 'UTC'` when it is another zone. PostgreSQL then reads a time
without an offset as UTC, as Restly does: `now()` gives UTC for a column
without a time zone, and psycopg gives back each time in UTC, as asyncpg
does. So the functions in your SQL work in UTC too:
`date_trunc('day', created_at)` gives the days of UTC. asyncpg is different
in one place: it converts a `datetime` without an offset that your code
passes for a column with a time zone itself, in the time zone of the Python
process; see [Keep times in UTC](#utc-times).

For the days of another zone, name the zone in the SQL. For a column with a
time zone, write `date_trunc('day', created_at AT TIME ZONE 'Europe/Amsterdam')`.
A column without a time zone holds UTC without the offset, so give it the
zone UTC first:
`date_trunc('day', starts_at AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Amsterdam')`.

Each of these options changes only the connection. Restly changes no setting
of the database itself, such as `journal_mode=WAL` on SQLite, which would
stay after the process exits.

These defaults apply only to the URL form. If you pass `engine=`,
`make_session=` or a session generator, Restly uses your object as it is. To
turn off any of these defaults, build the engine yourself. Such an engine
keeps the time zone of its PostgreSQL sessions. To put them in UTC, pass
`connect_args={"server_settings": {"timezone": "UTC"}}` to
`create_async_engine()` for asyncpg, or
`connect_args={"options": "-c timezone=UTC"}` for psycopg.

(session-factory-defaults)=

### Session factory defaults

When {func}`fr.configure() <fastapi_restly.db.configure>` builds the session
factory from a URL or an engine, it sets these SQLAlchemy session options:

| Factory | Autoflush | Expire on commit |
|---|---|---|
| Async `async_sessionmaker` | `False` | `False` |
| Sync `sessionmaker` | SQLAlchemy default (`True`) | `False` |

Both factories use `expire_on_commit=False`, so ORM objects can still be read
after a route commits. Restly's write handlers commit inside the request.
After the commit, Restly builds the response from the committed object, and an
`after_action_commit` hook may read it too. With `expire_on_commit=True`, the
commit expires those attributes, so each read loads them from the database
again. In async code, that read raises `MissingGreenlet`: the response is
built in plain async code, and SQLAlchemy cannot load from the database there
without an `await`. In sync code, the read works, but building the response
now needs the database. And in both, `snapshot(new)` in `after_action_commit`
is empty, because it reads only the loaded attributes. So Restly warns with
{class}`RestlyExpireOnCommitWarning <fastapi_restly.exc.RestlyExpireOnCommitWarning>`
once for each view that writes through a session that expires on commit.

The autoflush option is different for async and sync sessions. Async sessions
turn autoflush off, because autoflush can make a read send a write to the
database, and async code must do database work only at an explicit `await`.
Restly's async CRUD helpers flush when the writes must reach the database.
Sync sessions keep SQLAlchemy's default, so queries see changes that are still
pending in the session.

### The session dependency

`fr.AsyncSessionDep` and `fr.SessionDep` wrap the session source as a FastAPI
sub-dependency. The source is Restly's built-in generator, or the generator
that you passed to `fr.configure()` before you registered the routes. Because
the source is a dependency, FastAPI caches its session for the request, so
your own `Depends(get_db)` gets the same session without any change to the
view. The wrapper checks for changes that were not committed before the source
cleans up. The built-in generators roll back and close the session at the end.

Restly keeps its configuration in a private context object. Its own tests and
fixtures use that object to keep their state apart. It is not a public way to
use several databases.

## See also

- [Deploying](deploying.md): engine settings for production, and Alembic.
- [Testing](testing.md): how the test fixtures use this configuration.
- {func}`fr.configure() <fastapi_restly.db.configure>`: every argument.
