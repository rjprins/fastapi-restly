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
selectable, for example through an environment-backed setting:

```python
# myapp/main.py
import os

import fastapi_restly as fr

fr.configure(
    async_database_url=os.environ.get(
        "DATABASE_URL", "sqlite+aiosqlite:///./app.db"
    )
)
```

Then select the test value before importing the application, and configure the
suite from the application configuration already in force:

```python
# conftest.py
import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test.db"

import fastapi_restly as fr

from myapp.main import app  # noqa: E402
from myapp.models import Base  # noqa: E402

fr.testing.configure_tests(
    app=app,
    base=Base,
    create_all=True,
)
```

A `.env` file fits this pattern as long as explicit process environment values
take precedence over file values. Avoid loading dotenv with an “override
existing variables” option in application code; that makes the test unable to
select its database before import. If import-time settings are awkward, pass an
explicit test settings object to an app factory instead.

That is the whole setup. Until you call
{func}`configure_tests() <fastapi_restly.testing.configure_tests>` the plugin does
nothing: its fixtures are there, but none of them act on a suite that has not
asked for them.

The `testing` extra installs `pytest-asyncio`, which the async session fixture
needs. Give it a loop scope in `pyproject.toml`, or it prints a deprecation
warning on every run:

```toml
[tool.pytest.ini_options]
asyncio_default_fixture_loop_scope = "function"
```

Your tests themselves stay synchronous, even when your application is async. See
[writing async tests](#writing-async-tests) if you would rather they did not.

## What you get

Four things, which is about what any database-backed suite needs.

**One database configuration.** `configure_tests()` records the session sources
your application already configured; it does not create or replace them. If the
database configuration changes later, during collection or lifespan startup,
the suite fails instead of letting schema setup, cleanup and requests disagree.
Selecting a disposable test database remains the application's responsibility.
An environment variable, an explicit test settings object, or an app factory are
all good ways to do that.

**A schema that is already there.** Tables are created once, before the first
test, either from your models with `create_all=True` or by running your
migrations with `alembic_upgrade=True`.

**A clean database in every test.** Everything a test writes is rolled back when
it finishes, so no test sees another's rows and the suite does not care what
order it runs in. There is no teardown to write and no database to rebuild
between tests, which is what keeps a suite fast as it grows. See
[savepoints and rollback](#savepoints-and-rollback) for how that works, and
[cleaning up between tests](#cleaning-up-between-tests) for the two cases it
cannot serve.

**A client wired to your app.** `restly_client` sends real requests to the app
you passed, and those requests roll back with everything else.

## A first test

A test needs nothing but the client:

```python
# test_users.py
def test_create_and_fetch_user(restly_client):
    response = restly_client.post(  # asserts 201 automatically
        "/users/", json={"name": "Jane", "email": "jane@example.com"}
    )
    user_id = response.json()["id"]

    data = restly_client.get(f"/users/{user_id}").json()  # asserts 200
    assert data["name"] == "Jane"
```

The user this test creates is gone by the time the next test runs. To work
against the database directly instead of through the API, ask for a session
fixture; see [the fixture reference](#pytest-fixture-reference).

## Test databases and migrations

`base=Base` names your models. It is what `configure_tests()` cleans between
tests, and what it builds the schema from if you ask it to. Pass your declarative
base, or its `MetaData`.

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
application.
A stock `env.py` that reads that URL therefore migrates your test database, not
whatever it would otherwise resolve on its own. Pass a path if the config lives
somewhere else:

```python
fr.testing.configure_tests(
    app=app,
    base=Base,
    alembic_upgrade="backend/alembic.ini",
)
```

**Yourself**, by passing neither. Restly then leaves the schema alone, which is
what you want when your suite already builds it or your migrations are not
Alembic. Build it once per session rather than per test: each test runs inside a
transaction that rolls back, so tables created inside one are discarded with it.

See [Migrations with Alembic](deploying.md#migrations-with-alembic) for
production migration setup.

## Cleaning up between tests

Every test starts from a clean database, and `db_cleanup` decides how. The
default suits most suites; the other two exist for what it cannot serve.

**`"rollback"`**, the default, wraps each test in a transaction and rolls it back
at the end, through the savepoints described [below](#savepoints-and-rollback).
Nothing is ever committed, which is what makes it the fastest option, and it
leaves reference data your migrations seeded untouched. One combination is
refused up front: when the application has only an async sessionmaker using
asyncpg, the pinned connection cannot serve `restly_client`'s own event loop.
Configure a sync sessionmaker for the same database as well, or use `"delete"`.

**`"delete"`** empties the tables before each test instead, and lets writes
commit for real. It is slower and wants a database of its own, but the rows the
last test wrote are still there when the run ends, which is what makes them
inspectable.

**`"none"`** cleans nothing and leaves that to you. Neither of the others fits a
suite that drives a browser or another process, which cannot see uncommitted
data and whose parallel workers would clean the shared database out from under
each other.

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

## RestlyTestClient

The `restly_client` fixture wraps this client for you; construct it directly
when you are testing without the fixtures:

```python
from fastapi_restly.testing import RestlyTestClient

with RestlyTestClient(app) as client:
    response = client.get("/users/")
```

Enter it. Starlette runs an application's `lifespan` startup and shutdown only
inside the context manager, so a client built and used outside one skips whatever
`lifespan=` sets up. The `restly_client` fixture enters it for you, once per
test.

{class}`RestlyTestClient <fastapi_restly.testing.RestlyTestClient>` is intentionally sync-only. It still works for testing
async FastAPI routes and {class}`AsyncRestView <fastapi_restly.views.AsyncRestView>` endpoints.

Each request asserts a default status code and, on mismatch, raises an
`AssertionError` that includes the response body:

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
400"; it does **not** skip the assertion. To inspect an error response
yourself, pass the error code you expect.

## Writing async tests

You probably do not need them. `restly_client` is synchronous and drives async
routes and `AsyncRestView` endpoints perfectly well, so a suite for an async
application can be written entirely with `def` and usually should be. Async tests
buy little here, and they cost you an event-loop setting, a class of confusing
collection errors when it is wrong, and the ability to query the database from
`pdb` (see [inspecting the database](#inspecting-the-database)).

Write them when a test has to await something itself: `restly_async_session` to
set up rows directly, or one of your own coroutines. Then put pytest-asyncio into
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

A [`RestlyTestClient`](#restlytestclient) wrapping the `restly_app` fixture.
Requests made through it are rolled back with the rest of the test. On its own,
in a suite that never called `configure_tests()` and requests no session
fixture, **nothing rolls them back**: a client-only test commits real rows to
the configured database.

### `restly_session`

**Scope:** `function`

A SQLAlchemy `Session` on a pinned connection whose outer transaction is never
committed. Each request during the test builds its own real session that joins
that transaction through a savepoint (SQLAlchemy's `create_savepoint` mode), so
`commit()` and `rollback()` behave as in production while nothing persists past
the test. The fixture skips automatically if no sync session source is
configured at all.

A configured `sync_session_generator` is bypassed during rollback isolation, so
the request builds its own session on the fixture's connection instead. What is
lost is anything the generator body runs per session, a `SET search_path` for
example. Configure a sync sessionmaker as well: with only a generator the fixture
has nothing to build the isolated session from, and setup raises. Delete mode
rejects custom generators because it cannot prove that the generator and the
sessionmaker use the same database; `db_cleanup="none"` leaves them untouched.

`fr.open_session()` resolves the same factory `SessionDep` does, so it too
yields a session on the fixture's connection during a test.

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
(and `fr.open_async_session()`) the same way `restly_session` handles
`sync_session_generator`, including the raise when no async sessionmaker is
configured.

Usage mirrors `restly_session`, with `await`:

```python
async def test_user_created(restly_async_session):
    user = User(name="Bob")
    restly_async_session.add(user)
    await restly_async_session.commit()

    result = await restly_async_session.get(User, user.id)
    assert result.name == "Bob"
```

> **Note:** When both a sync and an async session source are configured,
> `restly_async_session` reuses the connection `restly_session` opens, so the
> two fixtures share one transaction and a write committed through either is
> visible to the other within the same test. The async session runs over that
> sync connection, so point both at the same database; the async driver itself
> is not exercised in this mode.

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
The fixture opens one connection, begins a transaction on it, and points Restly's
session factory at that connection in `create_savepoint` mode. Every session
built during the test, including the ones requests build, joins that transaction
through a savepoint.

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
(Pdb) restly_client.get("/users/").json()
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
