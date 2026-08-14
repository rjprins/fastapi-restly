# Test APIs with RestlyTestClient and Fixtures

FastAPI-Restly ships a test client with sensible status-code assertions and a
small pytest plugin that gives every test a clean database. You configure the
suite once, and then write tests that only talk to your API.

## Setup

Install the testing extra:

```bash
pip install "fastapi-restly[testing]"
```

The `testing` extra is independent of `standard`, which is runtime-only. Neither
carries a database driver, since Restly does not choose one for you, so install
the driver your test database needs as well. The examples below use SQLite
through `aiosqlite`:

```bash
pip install aiosqlite
```

The extra registers a `pytest11` entry point, so pytest auto-loads the Restly
fixtures. If your project disables plugin autoloading, register the plugin
manually in `conftest.py`:

```python
pytest_plugins = ["fastapi_restly.pytest_fixtures"]
```

The application still owns its database configuration. Make that configuration
selectable by building the application in a factory: a `create_app()` function
that receives its settings, configures Restly, and returns the app. Configuring
inside the factory keeps database setup out of module import, so a test can
build the application it wants instead of racing to change the environment
first:

```python
# myapp/main.py
import os

import fastapi_restly as fr
from fastapi import FastAPI

from .api import register_views


def create_app(database_url: str | None = None) -> FastAPI:
    fr.configure(
        async_database_url=database_url
        or os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./app.db")
    )
    app = FastAPI()
    register_views(app)
    return app
```

Run it with `uvicorn --factory myapp.main:create_app`. A factory whose
settings can be built without the environment, as the SQLite fallback above
allows, may also keep a module-level `app = create_app()` for a plain
`uvicorn myapp.main:app`. The test suite then builds the application it tests,
naming the test database explicitly, and hands the result to
`configure_tests()`:

```python
# conftest.py
import fastapi_restly as fr
from myapp.database import Base
from myapp.main import create_app

app = create_app("sqlite+aiosqlite:///./test.db")

fr.testing.configure_tests(
    app=app,
    base=Base,
    create_all=True,
)
```

Restly's configuration is process-wide and the last `configure()` call wins,
which is what makes this work: the factory call in `conftest.py` re-points the
process at the test database before `configure_tests()` freezes it. That holds
even when importing `myapp.main` already built a module-level default app,
since the conftest's factory call runs after the import and before the freeze.
A factory that *requires* its settings, like the PostgreSQL one below, must
not build an app at import time: keep it on the `--factory` form, or importing
it in `conftest.py` would again require the environment variable to be set
before the import, which is exactly the requirement the factory form lifts
from tests. This safety only covers modules the conftest itself imports before
`configure_tests()` runs. A module built elsewhere and imported later, such as
an ASGI entrypoint a test file reaches for directly, still calls its
module-level factory after the freeze and fails with
`RestlyConfigurationError`. Keep default-app modules out of the import graph a
managed suite pulls in after collection.

(factory-apps-share-one-configuration)=
The apps a factory builds are therefore not independent. Each call re-points
the one process-wide configuration, so only the most recently built app should
serve requests. An app built earlier would read the newer database while still
owning its original engine. In this suite that is the conftest's app, and the
module-level default never serves a request. For the same reason the common
per-test factory fixture (`@pytest.fixture` returning `create_app(...)`) does
not fit a managed suite: once `configure_tests()` has recorded the
configuration, every later factory call that configures the database raises
`RestlyConfigurationError`. Build the app once in `conftest.py`.

An application that instead calls `fr.configure()` at module import time can
still be tested: select the value before importing it, and note the import
order that the factory form avoids:

```python
# conftest.py
import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test.db"

import fastapi_restly as fr

from myapp.main import app  # noqa: E402
from myapp.database import Base  # noqa: E402

fr.testing.configure_tests(app=app, base=Base, create_all=True)
```

A `.env` file is convenient in development, when the settings class enables
one (the [production template](#production-main-template) does not; the [SaaS
example](examples.md) does). Keep it out of the suite's way. When the conftest
builds the app through the factory, pass explicit settings and disable the
settings class's env file (with pydantic-settings:
`Settings(database_url=..., _env_file=None)`). Explicitly passed values
already outrank the file, but values the test leaves unset, such as pool
sizes, would still come from a developer's local `.env`, and the suite would
behave differently locally than in CI. `_env_file=None` disables only that file
source; an exported shell variable still reaches `Settings()` in tests exactly
as it would in production, so pin a setting explicitly if the suite must not
vary with it. Under the env-before-import fallback, explicit process
environment values must take precedence over file values, so avoid dotenv's
“override existing variables” option. It would override the database the test
selected.

The `testing` extra installs `pytest-asyncio`. Set its default fixture loop scope
in `pyproject.toml`, or pytest-asyncio prints a deprecation warning on every run:

```toml
[tool.pytest.ini_options]
asyncio_default_fixture_loop_scope = "function"
```

That completes the setup for a managed suite.
{func}`configure_tests() <fastapi_restly.testing.configure_tests>` opts every
test into schema setup and the selected database cleanup. Without that call,
the client fixtures still run but do not start database isolation. For backward
compatibility, a test that explicitly requests `restly_session` still runs
under rollback isolation, including its requests through `restly_client`.
`restly_async_session` does the same for requests through
`restly_async_client`. A client-only test without `configure_tests()` commits
normally.

Your tests can stay synchronous, even when your application and its only
database driver are async. See [writing async tests](#writing-async-tests) when
a test also needs direct async database access.

### Async-only PostgreSQL

An application configured only with async PostgreSQL can keep its ordinary
pooled engine in tests. You do not need a test-only `NullPool` engine. Build
the app with [the production template's factory](#production-main-template),
whose lifespan owns the engine: it creates the engine from its settings and
hands it to Restly with `fr.configure(app, async_engine=engine)`.

Use a production URL such as `postgresql+asyncpg://...` in deployment. That
factory takes `Settings`, not a bare URL, so build one in `conftest.py` with
the test database and the local `.env` disabled:

```python
# conftest.py
import fastapi_restly as fr
from myapp.database import Base
from myapp.main import create_app
from myapp.settings import Settings

app = create_app(
    Settings(database_url="postgresql+asyncpg://...", _env_file=None)
)

fr.testing.configure_tests(
    app=app,
    base=Base,
    create_all=True,
)
```

Restly neither rebuilds the engine nor changes its pool. The engine belongs to
the app the factory built, and that app's lifespan owns the dispose call, in
production and in tests alike.

## What you get

Four things, which is about what any database-backed suite needs.

**One database configuration.** `configure_tests()` records the session sources
your application already configured; it does not create or replace them. If the
database configuration changes later, during collection or lifespan startup,
the suite fails instead of letting schema setup, cleanup and requests disagree.
Selecting a disposable test database remains the application's responsibility.
An app factory receiving the test database explicitly is the recommended way to
do that. An environment variable selected before import also works.

**A schema that is already there.** Tables are created once, before the first
test, either from your models with `create_all=True` or by running your
migrations with `alembic_upgrade=True`.

**A cleanup policy for every test.** With the default cleanup, everything a test
writes is rolled back when it finishes, so no test sees another's rows and the
suite does not care what order it runs in. There is no teardown to write and no
database to rebuild between tests, which is what keeps a suite fast as it
grows. See [savepoints and rollback](#savepoints-and-rollback) for how that
works, and [cleaning up between tests](#cleaning-up-between-tests) for the two
cases it cannot serve.

**Clients wired to your app.** `restly_client` sends requests from ordinary
`def` tests. `restly_async_client` sends them from `async def` tests on the same
event loop as `restly_async_session`. Both run the app's lifespan and roll their
requests back with everything else under the default cleanup.

## A first test

A test needs nothing but the client:

```python
# test_users.py
def test_create_and_fetch_user(restly_client):
    response = restly_client.post(  # asserts 201 automatically
        "/users", json={"name": "Jane", "email": "jane@example.com"}
    )
    user_id = response.json()["id"]

    data = restly_client.get(f"/users/{user_id}").json()  # asserts 200
    assert data["name"] == "Jane"
```

With the default cleanup, the user this test creates is gone by the time the
next test runs. To work against the database directly instead of through the
API, ask for a session fixture, or use `fr.open_session()` /
`fr.open_async_session()` directly in a unit test. Database isolation belongs
to the test, not to the client fixture, so either form receives the same
cleanup; see [the fixture reference](#pytest-fixture-reference).

## Test databases and migrations

`base=Base` names your models. It is what `configure_tests()` cleans between
tests, and what it builds the schema from if you ask it to. Pass your declarative
base, or its `MetaData`.

A base only knows the models that have been imported by the time the suite
freezes. Building the app first is what imports them, since composition reaches
every view and each view imports its model. A conftest that reaches for `Base`
without building the app should import `api.py` as well, the way
`alembic/env.py` does; see
[Compose in one place](#compose-in-one-place).

Who builds the schema is a separate choice, and there are three answers.

**From your models**, with `create_all=True`, which builds the tables the
way {func}`fr.db.create_all() <fastapi_restly.db.create_all>` does. This is the
quickest route, and the right one when migrations are not part of what you are
testing. Point it at a database you are willing to lose: a leftover file from an
older run keeps its stale tables, since creating a schema never drops one.

**From your migrations**, with `alembic_upgrade=True`, which runs
`alembic upgrade head` before the first test. Restly resolves `alembic.ini`
relative to your project rather than to the directory you happened to run pytest
from, and sets `sqlalchemy.url` on the config to the database configured by the
application. An `env.py` that reads that setting therefore migrates your test
database, not whatever it would otherwise resolve on its own.

If the application configures only an async URL such as
`postgresql+asyncpg://...` or `sqlite+aiosqlite://...`, its Alembic `env.py`
must be able to open that async URL. Create the environment with Alembic's async
template (`alembic init -t async ...`) or adapt an existing `env.py` using
Alembic's
[asyncio recipe](https://alembic.sqlalchemy.org/en/latest/cookbook.html#using-asyncio-with-alembic).
The standard synchronous template cannot open it. When the application
configures both synchronous and async database URLs, Restly passes the
synchronous URL to Alembic.

Pass a path if the config lives somewhere else:

```python
fr.testing.configure_tests(
    app=app,
    base=Base,
    alembic_upgrade="backend/alembic.ini",
)
```

**Yourself**, by passing neither. Restly then leaves the schema alone, which is
what you want when your suite already builds it or your migrations are not
Alembic. Build it once per session rather than per test. Under the default
rollback mode, a table created inside a test belongs to that test's transaction
and is discarded with it.

See [Migrations with Alembic](deploying.md#migrations-with-alembic) for
production migration setup.

## Cleaning up between tests

The `db_cleanup=` argument to `configure_tests()` decides what Restly does with
the database around each test. The default suits most suites; the other two
exist for cases it cannot serve.

**`"rollback"`**, the default, wraps each test in a transaction and rolls it back
at the end, through the savepoints described [below](#savepoints-and-rollback).
Nothing is ever committed, which is what makes it the fastest option, and it
leaves reference data your migrations seeded untouched.

**`"delete"`** empties the tables before each test instead, and lets writes
commit for real. It is slower and wants a database of its own, but the rows the
last test wrote are still there when the run ends, which is what makes them
inspectable.

**`"none"`** cleans nothing and leaves that to you. Neither of the others fits a
suite that drives a browser or another process, which cannot see uncommitted
data and whose parallel workers would clean the shared database out from under
each other.

In an application configured with only an async database, use `restly_client`
when a test only sends HTTP requests and `restly_async_client` when it also needs
direct async database access. The synchronous client runs the application on a
different event loop from async test fixtures. Under rollback, a test that
requests it together with `restly_async_session` stops during fixture setup
with a `RestlyConfigurationError`. The error directs you to
`restly_async_client`, and `fr.open_async_session()` fails with the same
guidance at the moment it opens the session. Drivers that bind pooled
connections to one event loop, such
as asyncpg, require the same separation under `"delete"` and `"none"`.

Switch mode for one run without editing the suite:

```bash
pytest --restly-db-cleanup=delete
RESTLY_DB_CLEANUP=delete pytest
```

The flag beats the environment variable, which beats the argument. Any mode other
than the default announces itself in pytest's header, so a flag left over from a
debugging session cannot quietly change what a suite does. pytest prints that
header at normal verbosity only, so `-q` hides it.

### Reference data and cleaning

Delete mode empties the tables `base=` declares, including the ones your
migrations seeded with reference data, and nothing puts those rows back. Name
them and they are left alone:

```python
fr.testing.configure_tests(
    app=app,
    base=Base,
    alembic_upgrade=True,
    db_cleanup="delete",
    db_cleanup_exclude=["country", "role"],
)
```

Excluded tables are shared by every test, so a write to one does carry over.
Naming a table that does not exist raises, since a typo would otherwise empty the
very table you meant to protect.

## Test clients

{class}`RestlyTestClient <fastapi_restly.testing.RestlyTestClient>` is the
synchronous client behind the `restly_client` fixture. The fixture constructs
and enters it for you; use it directly when testing without the fixtures:

```python
from fastapi_restly.testing import RestlyTestClient

with RestlyTestClient(app) as client:
    response = client.get("/users")
```

Enter it. Starlette runs an application's `lifespan` startup and shutdown only
inside the context manager, so a client built and used outside one skips whatever
`lifespan=` sets up. The `restly_client` fixture enters it for you, once per
test.

Although it is synchronous, `RestlyTestClient` still tests async FastAPI routes
and {class}`AsyncRestView <fastapi_restly.views.AsyncRestView>` endpoints.
{class}`AsyncRestlyTestClient <fastapi_restly.testing.AsyncRestlyTestClient>`
provides the same request methods and assertions for async tests; the
`restly_async_client` fixture constructs it and runs the app lifespan for you.

Both clients assert a default status code for each request and, on mismatch,
raise an `AssertionError` that includes the response body:

| Method   | Default expected status |
|----------|-------------------------|
| `get`    | `200`                   |
| `post`   | `201`                   |
| `put`    | `200`                   |
| `patch`  | `200`                   |
| `delete` | `204`                   |

`AsyncRestView` and {class}`RestView <fastapi_restly.views.RestView>` do not generate `PUT` routes; the client's
`put` exists for [React Admin views](howto_react_admin.md) and custom routes.

Override the expectation when testing error paths:

```python
def test_not_found(restly_client):
    restly_client.get("/users/999", assert_status_code=404)
```

Passing `assert_status_code=None` relaxes the check to "any status below
400"; it does **not** skip the assertion. Pass the error code you expect when
you know it. To make a request with no Restly status assertion at all, use the
generic `request()` method:

```python
response = restly_client.request("GET", "/users/999")
```

The async client offers the same escape hatch with `await
restly_async_client.request(...)`.

## Writing async tests

You probably do not need them. `restly_client` is synchronous and drives async
routes and `AsyncRestView` endpoints perfectly well, so a suite for an async
application can be written entirely with `def` and usually should be. Async tests
buy little here, and they cost you an event-loop setting, a class of confusing
collection errors when it is wrong, and the ability to query the database from
`pdb` (see [inspecting the database](#inspecting-the-database)).

Write them when a test has to await something itself: `restly_async_session` or
`fr.open_async_session()` to set up rows directly, or one of your own
coroutines. Use `restly_async_client` for HTTP in the same test. In an
async-only rollback suite, a test that combines `restly_client` and
`restly_async_session` stops during fixture setup with a
`RestlyConfigurationError`: the synchronous client and the async fixture use
different event loops and cannot share one test transaction. The error tells
you to use `restly_async_client`.

```python
async def test_direct_setup_and_request(restly_async_client, restly_async_session):
    restly_async_session.add(User(name="Alice"))
    await restly_async_session.commit()

    response = await restly_async_client.get("/users")
    assert response.json()["total_count"] == 1
```

Put pytest-asyncio into
[auto
mode](https://pytest-asyncio.readthedocs.io/en/latest/concepts.html#test-discovery-modes),
which collects `async def` tests without marking each one:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
```

Without that, or an equivalent
[anyio](https://anyio.readthedocs.io/en/stable/testing.html) setup, async tests
fail to collect or produce confusing errors. Sync tests keep working alongside
them. The [pytest-asyncio
reference](https://pytest-asyncio.readthedocs.io/en/latest/reference/configuration.html)
covers the rest of its settings.

## Pytest fixture reference

These are the fixtures the plugin registers, with their scope and exact
behavior.

### `restly_app`

**Scope:** `function`

Returns the app you passed to `configure_tests(app=...)`. Without one it returns
a bare `FastAPI()`, and every request answers 404; override this fixture in your
`conftest.py` if you would rather supply the app that way.

### `restly_client`

**Scope:** `function`

A [`RestlyTestClient`](#test-clients) wrapping the `restly_app` fixture.
In a configured suite, requests follow its [cleanup mode](#cleaning-up-between-tests):
the default rolls them back with the rest of the test, while `"delete"` and
`"none"` let them commit. On its own, in a suite that never called
`configure_tests()` and requests no session fixture, **nothing rolls them
back**: a client-only test commits real rows to the configured database.

For an async-only application under rollback, Restly opens the transaction on
the same event loop the synchronous client uses to run the application. This is
why a normally pooled asyncpg engine works even though the test function itself
is synchronous.

### `restly_async_client`

**Scope:** `function`

An [`AsyncRestlyTestClient`](#test-clients) wrapping `restly_app`, with the same
default status assertions as `restly_client`. It runs the application's lifespan
and shares the event loop used by `restly_async_session` (and, under rollback,
the same transaction). Use it whenever a test needs both HTTP and direct async
database access.

### `restly_session`

**Scope:** `function`

Under the default rollback mode, a SQLAlchemy `Session` on a pinned connection
whose outer transaction is never committed. Each request during the test builds
its own real session that joins that transaction through a savepoint
(SQLAlchemy's `create_savepoint` mode), so `commit()` and `rollback()` behave
as in production while nothing persists past the test. The fixture skips
automatically if no sync session source is configured at all.

In a suite that called `configure_tests()`, a custom
`sync_session_generator` follows these rules. The same table applies to the
async `session_generator` and async sessionmaker.

| Cleanup mode | Generator behavior |
|---|---|
| `"rollback"` | Restly bypasses the generator and builds isolated sessions from the matching sessionmaker. If only the generator is configured, pytest stops before running tests with a configuration error that names the missing sessionmaker. Code in the generator body, such as `SET search_path`, does not run. |
| `"delete"` | If either custom generator is configured, pytest stops before running tests with a configuration error, even when a matching sessionmaker exists. Restly cannot verify that the generator writes to the database it would clean. Use the sessionmaker as the application session source, or choose `"none"`. |
| `"none"` | Restly leaves the generator untouched. The public session fixture uses the matching sessionmaker when one exists; with only a generator it skips because there is no sessionmaker from which to construct the fixture session. |

Under rollback, `fr.open_session()` resolves the same factory `SessionDep` does,
so it too yields a session on the test's pinned connection even when
`restly_session` is not requested. This makes database-only unit tests a
first-class use case; the public fixture is only a convenient ready-made
`Session`.

Under any [cleanup mode](#cleaning-up-between-tests) other than `rollback` this
fixture yields a plain session on the configured database instead, since rolling
it back would undo the writes those modes exist to commit.

A committed write can be read back within the same test:

```python
def test_user_created(restly_session):
    user = User(name="Alice")
    restly_session.add(user)
    restly_session.commit()

    result = restly_session.get(User, user.id)
    assert result.name == "Alice"
```

### `restly_async_session`

**Scope:** `function`

The async version of `restly_session`. Awaiting it in a test body means
[writing an async test](#writing-async-tests). In async-only projects it needs only
`fr.configure(async_database_url=...)`. It skips automatically if no async
session source is configured at all. It handles a configured `session_generator`
(and `fr.open_async_session()`) according to the generator table above, using
the async sessionmaker where the sync fixture uses the synchronous one.

Pair it with `restly_async_client`, not `restly_client`, in an async-only
rollback test. This keeps every use of the pinned connection on pytest's event
loop.

Under rollback, as on the sync side, `fr.open_async_session()` is isolated even
when this public fixture is not requested. Use that form when a unit test
naturally owns several short-lived sessions or when application service code
already opens its own.

Usage mirrors `restly_session`, with `await`:

```python
async def test_user_created(restly_async_session):
    user = User(name="Bob")
    restly_async_session.add(user)
    await restly_async_session.commit()

    result = await restly_async_session.get(User, user.id)
    assert result.name == "Bob"
```

> **Note (rollback only):** When both synchronous and async session sources are
> configured, Restly runs the async fixture over the test's pinned synchronous
> connection. The two fixtures therefore share one transaction, and a write
> committed through either is visible to the other within the test. The async
> driver itself is not exercised. Under `"delete"` and `"none"`, each fixture
> uses its configured sessionmaker independently; there is no shared connection
> or outer transaction, and writes cross between them only after a real commit,
> subject to the database's ordinary isolation rules.

### `restly_project_root`

**Scope:** `function`

Walks up from the requesting test file until it finds a `pyproject.toml` and
returns that directory as a `Path`. This is a convenience for locating project
files (migration configs, test data) from tests. Because discovery is anchored
to the test file rather than the working directory, it returns the same root
regardless of where pytest was invoked, and in a monorepo each test resolves to
its own sub-project's root.

## Savepoints and rollback

The default mode follows SQLAlchemy's own recipe for test suites, [joining a
session into an external
transaction](https://docs.sqlalchemy.org/en/20/orm/session_transaction.html#session-external-transaction).
The plugin's internal test scope opens one connection, begins a transaction on
it, and points Restly's session factory at that connection in
`create_savepoint` mode. Every session built during the test, including direct
sessions and the ones requests build, joins that transaction through a
savepoint. Public client and session fixtures consume this scope; none of them
owns it.

A `commit()` therefore releases a savepoint rather than reaching the database,
and a `rollback()` discards only that session's own work, so both behave the way
they do in production. The outer transaction is never committed: closing the
connection at the end of the test undoes everything at once.

That is also what makes it fast. Cleanup is one rollback on a connection that is
already open, so it costs the same whether a test wrote one row or a thousand,
and it does not slow down as your schema grows.

Explicit transaction blocks behave as in production too: `with
restly_session.begin(): ...` and its async form commit on success and roll back
on error, scoped to the outer transaction so nothing survives the test.

## Inspecting the database

Opening `psql` while a test runs under the default mode finds nothing, and so
does opening it afterwards. The rows live in an uncommitted transaction on a
single connection, which no other process can read, and the rollback removes them
when the test ends. Two ways to see them anyway.

**From inside the test.** `pytest --pdb` stops at the failure while the
transaction is still open, before any fixture tears down, and `restly_client`
works from there:

```
(Pdb) restly_client.get("/users").json()
{'data': [{'id': 1, 'name': 'Jane'}], 'total_count': 1, ...}
```

Querying through your own API like this works whether the suite is sync or async,
because the client is synchronous either way. Querying a session directly works
only in a sync suite, with `restly_session.execute(...)`. In an async one
`restly_async_session.execute(...)` hands back a coroutine that nothing awaits,
and reaching for its `sync_session` raises `MissingGreenlet`.

**With ordinary tools.** Run once in [delete mode](#cleaning-up-between-tests)
and the rows are committed, and still there when the run ends:

```bash
pytest --restly-db-cleanup=delete -k test_the_broken_one
psql myapp_test -c 'select * from "user"'
```

Cleaning happens before each test rather than after, which is what leaves the
failing test's rows in place. Point the suite at a file or a server for this; an
in-memory database is gone as soon as pytest exits.
