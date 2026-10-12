# Technical Details

This page documents the implementation behaviour beneath FastAPI-Restly's
public API: how schemas are generated and derived, how view classes register
their routes, how list parameters are assembled, and which session defaults
the framework sets.

## View Classes and Registration

The generated schemas feed the view layer, which turns a model and a schema
into registered FastAPI routes.

### AsyncRestView and RestView

`AsyncSessionDep` and `SessionDep` wrap a session source as a FastAPI
sub-dependency. The source is either Restly's built-in generator or the custom
generator passed to {func}`configure() <fastapi_restly.db.configure>` before
route registration. Making that source a dependency lets FastAPI share its
cached session with the application's `Depends(get_db)`, without rewriting
view annotations. The wrapper checks for uncommitted changes before the source
cleans up. The built-in generators roll back and close on exit.

(list-parameters-lifecycle)=
## List Parameters Lifecycle

List endpoints accept URL query parameters of the form
``name=John``, ``age__gte=18``, ``sort=-created_at``, and
``page=2&page_size=50``. The full operator surface (`__ne`, `__isnull`,
`__contains`, `__icontains`, and more) is documented in
[Filtering & Pagination](howto_query_modifiers.md).

During {meth}`before_include_view() <fastapi_restly.views.BaseRestView.before_include_view>`, the framework freezes a single class-level
attribute, {attr}`cls.schema_list_params <fastapi_restly.views.BaseRestView.schema_list_params>`: the list params, a Pydantic model generated
by {func}`derive_schema_list_params(cls.schema_response, cls.model, pagination=cls.pagination) <fastapi_restly.query.derive_schema_list_params>`. It covers pagination, sorting, and one filter
parameter per field of the response class that maps to a filterable column on the
model, with optional operator suffixes. A client can filter and sort only on
what it can see. It is generated once per registration
and never re-derived.

A route reads the list params through a dependency, not as a FastAPI query model.
FastAPI splits a query model into separate parameters only when it is the
route's only query parameter, so a dependency's `?api_key=` would collapse
the filters into one required object. Restly validates the model from the
query string itself and documents each key as its own OpenAPI parameter,
next to whatever else the route reads.

Custom dialects (e.g. react-admin's
[`AsyncReactAdminView` / `ReactAdminView`](howto_react_admin.md)) live as
parallel view classes that bypass {func}`apply_list_params <fastapi_restly.query.apply_list_params>` entirely and
implement their own request/response contract.

## Restly Runtime Configuration

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

## Engine Defaults

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

All of this rides on the URL rung alone. Pass `engine=`, `make_session=` or a
session generator and Restly leaves your object exactly as it arrived, so
building the engine yourself is how you decline any of these defaults.

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

## Session Factory Defaults

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

## See Also

- [Filtering & Pagination](howto_query_modifiers.md): the full
  filter, sort, and pagination reference.
