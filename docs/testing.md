# Testing

Restly ships a pytest plugin and two test clients. You build your application
against a test database in `conftest.py`, and then call
{func}`fr.testing.configure_tests() <fastapi_restly.testing.configure_tests>`
once. After that, your tests only talk to your API. The plugin gives every
test four things:

- **One database configuration.** `configure_tests()` records the session
  sources that your application configured. It does not create or replace
  them. If the database configuration changes later, while pytest collects the
  tests or while the app's lifespan starts, the suite fails. So the schema
  setup, the cleanup and the requests always use the same database. You must
  still choose a test database that the tests are allowed to change.
- **A schema that is already there.** The tables are created once, before the
  first test: from your models with `create_all=True`, or with your
  migrations with `alembic_upgrade=True`.
- **A clean database for each test.** By default, everything that a test
  writes is rolled back when the test ends. No test sees the rows of another
  test, and the order of the tests does not matter. You write no teardown
  code, and nothing rebuilds the database between tests, so the suite stays
  fast as it grows. [Choose how tests clean up](#cleaning-up-between-tests)
  describes the other modes.
- **Clients connected to your app.** `restly_client` sends requests from
  ordinary `def` tests. `restly_async_client` sends them from `async def`
  tests. Both run the app's lifespan, and the default cleanup rolls back their
  requests with everything else.

(test-setup)=

## Set up the test suite

Install the `testing` extra:

```bash
pip install "fastapi-restly[testing]"
```

The `testing` extra does not depend on `standard`, which is only for running
the app. Neither one installs a database driver, because Restly does not
choose one for you. So install the driver that your test database needs as
well. The examples below use SQLite through `aiosqlite`:

```bash
pip install aiosqlite
```

The package registers a `pytest11` entry point. So when the `testing` extra is
installed, pytest loads the Restly fixtures by itself. If your project turns off plugin autoloading, register
the plugin in `conftest.py`:

```python
pytest_plugins = ["fastapi_restly.pytest_fixtures"]
```

The application still owns its database configuration. To let a test choose
the database, build the application in a factory: a `create_app()` function
that receives its settings, configures Restly, and returns the app. The
factory keeps the database setup out of the module import, so a test can
build the application that it needs. It does not have to change the
environment before an import.

```python
# app/main.py
import os

import fastapi_restly as fr
from fastapi import FastAPI

from .tasks.views import TaskView

VIEWS = (TaskView,)


def create_app(database_url: str | None = None) -> FastAPI:
    fr.configure(
        async_database_url=database_url
        or os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./app.db")
    )
    app = FastAPI()
    for view in VIEWS:
        fr.include_view(app, view)
    return app
```

[Run the app](#running-the-app) shows how a server runs this factory. The
test suite builds its own application with the test database, and passes it
to `configure_tests()`:

```python
# conftest.py
import fastapi_restly as fr
from app.main import create_app
from app.models import Base

app = create_app("sqlite+aiosqlite:///./test.db")

fr.testing.configure_tests(
    app=app,
    base=Base,
    create_all=True,
)
```

An application that calls `fr.configure()` when its module is imported can
still be tested. Choose the database before you import the module, and keep
the import order:

```python
# conftest.py
import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test.db"

import fastapi_restly as fr

from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402

fr.testing.configure_tests(app=app, base=Base, create_all=True)
```

A `.env` file is useful in development, when the settings class reads one.
The [production template](#production-main-template) does not; the
[SaaS example](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/settings.py)
does. Keep it out of the test suite. When the conftest builds its own
settings, turn the file off (with pydantic-settings:
`Settings(database_url=..., _env_file=None)`). The values that you pass
explicitly already win over the file. But a value that the test does not
set, such as a pool size, would still come from the local `.env` of a
developer, and the suite would then behave differently on that machine than
in CI. `_env_file=None` turns off only the file. A variable that is exported
in the shell still reaches `Settings()` in tests, as it does in production,
so set a value explicitly if the suite must not depend on it. If you choose
the database with an environment variable before the import, as above, the
process environment must win over the file. So do not use the dotenv option
that overrides existing variables: it would replace the database that the
test chose.

The `testing` extra installs `pytest-asyncio`. Set its default fixture loop
scope in `pyproject.toml`, or pytest-asyncio prints a deprecation warning on
every run:

```toml
[tool.pytest.ini_options]
asyncio_default_fixture_loop_scope = "function"
```

That is the whole setup. With `configure_tests()`, every test gets the schema
setup and the cleanup that you chose. Without that call, the client fixtures
still work, but they do not isolate the database: see
[`restly_client`](#restly-client-fixture).

Your tests can stay sync, even when your application and its only database
driver are async. [Write async tests](#writing-async-tests) explains when a
test needs to be async.

:::{admonition} Details
:class: details

This works because Restly has
[one configuration per process](#one-configuration-per-process), and the last
`fr.configure()` call wins. The factory call in `conftest.py` points the
process at the test database. Then `configure_tests()` records that
configuration and freezes it. This also works when a module that the
conftest imports has already built an app: the conftest's factory call runs
after that import, and before the freeze.

A factory that *requires* its settings, like the PostgreSQL one below, must
not build an app when its module is imported. Keep it a factory. Otherwise
`conftest.py` would again need the environment variable before the import, and
the factory exists to remove that need. A module that builds an app when it
is imported works only if the conftest imports it before `configure_tests()`.
A module imported later, such as
an `asgi.py` that a test file imports, calls its factory after the freeze, and
fails with
{class}`RestlyConfigurationError <fastapi_restly.exc.RestlyConfigurationError>`.
This is one more reason why only the server should import `asgi.py`.

(factory-apps-share-one-configuration)=
For the same reason, the apps that a factory builds are not independent. Each
call changes the one configuration of the process, so only the app that was
built last should serve requests. An app built earlier would use the newer
database, but still own its old engine. In this suite, the app of the
conftest is the one that serves. A fixture that calls `create_app(...)` for
each test does not fit a managed suite: after `configure_tests()` has
recorded the configuration, each later factory call that configures the
database raises `RestlyConfigurationError`. Build the app once, in
`conftest.py`.
:::

(async-postgresql-tests)=

### Test an async-only PostgreSQL app

An application that uses only async PostgreSQL can keep its normal pooled
engine in tests. You do not need a special `NullPool` engine for tests. A
factory that reads its settings, like
[the production template](#production-main-template), takes `Settings` and
not a URL. So the conftest builds the settings, with the test database and
without the local `.env`. This is the `conftest.py` that `restly new`
generates:

```{literalinclude} ../example-projects/starter/tests/conftest.py
:language: python
```

`configure_tests()` does not build a new engine, and it does not change the
pool. The app's lifespan disposes the engine, in production and in tests. The
SaaS example tests its application-owned engine the same way
([`tests/conftest.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/tests/conftest.py)).

(first-test)=

## Write a first test

A test needs only the client:

```python
# test_users.py
def test_create_and_fetch_user(restly_client):
    response = restly_client.post(  # fails the test unless the status is 2xx
        "/users", json={"name": "Jane", "email": "jane@example.com"}
    )
    user_id = response.json()["id"]

    data = restly_client.get(f"/users/{user_id}").json()
    assert data["name"] == "Jane"
```

With the default cleanup, the user that this test creates is gone when the
next test runs. To work with the database directly instead of through the
API, ask for a session fixture, or use `fr.open_session()` or
`fr.open_async_session()` in a unit test. The database isolation belongs to
the test, not to the client fixture, so both ways get the same cleanup; see
[the fixture reference](#pytest-fixture-reference).

(test-databases-and-migrations)=

## Build the test schema

`base=` names your models. `configure_tests()` cleans the tables of these
models between tests, and builds the schema from them if you ask it to. Pass
your declarative base, or its `MetaData`.

Who builds the schema is a separate choice, with three answers.

**From your models**, with `create_all=True`. It builds the tables as
{func}`fr.db.create_all() <fastapi_restly.db.create_all>` does. This is the
quickest way, and the right one when you do not test your migrations. Use a
database that you can lose: a file left from an older run keeps its old
tables, because creating a schema never drops a table.

**From your migrations**, with `alembic_upgrade=True`. It runs
`alembic upgrade head` before the first test. Restly finds `alembic.ini`
from the root of your project, not from the directory where you started
pytest. It sets `sqlalchemy.url` in the Alembic config to the database that
the application configured. An `env.py` that reads this setting therefore
migrates your test database, and not another one. Restly keeps the driver
of this URL, so an async-only application needs an `env.py` made from
Alembic's async template, as
[Set up migrations with Alembic](#alembic) shows. When the application
configures both a sync and an async database, Restly passes the sync URL.

The SaaS example builds its test schema from its migrations
([`tests/conftest.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/tests/conftest.py)).

**Yourself**, when you pass neither. Restly then does not touch the schema.
Choose this when your suite already builds it, or when your migrations do not
use Alembic. Build the schema once per session, not once per test. Under the
default rollback mode, a table that a test creates belongs to the
transaction of that test, and the rollback removes it.

:::{admonition} Details
:class: details

A base knows only the models that have been imported when the suite freezes
the configuration. Building the app imports them, because the app imports
every view, and each view imports its model. A conftest that uses `Base`
without building the app should also import `app.main`, as `alembic/env.py`
does; see [Compose in one place](#compose-in-one-place).

Pass a path if the config is somewhere else:

```python
fr.testing.configure_tests(
    app=app,
    base=Base,
    alembic_upgrade="backend/alembic.ini",
)
```
:::

(cleaning-up-between-tests)=

## Choose how tests clean up

The `db_cleanup=` argument of `configure_tests()` decides what Restly does
with the database around each test. The default fits most suites. The other
two modes are for the cases that the default cannot serve.

**`"rollback"`**, the default, runs each test in a transaction and rolls it
back at the end, with the savepoints that
[Internals](#savepoints-and-rollback) describes. Nothing is ever committed.
This makes it the fastest mode, and it keeps the reference data that your
migrations added.

**`"delete"`** empties the tables before each test instead, and lets writes
commit for real. It is slower, and it needs a database of its own. But the
rows of the last test are still there when the run ends, so you can look at
them.

**`"none"`** cleans nothing, and leaves the cleanup to you. Use it for a suite
that drives a browser or another process. Such a process cannot see data
that is not committed, and parallel workers that share one database would
delete each other's rows.

:::{admonition} Details
:class: details

To change the mode for one run without editing the suite:

```bash
pytest --restly-db-cleanup=delete
RESTLY_DB_CLEANUP=delete pytest
```

The flag wins over the environment variable, and the variable wins over the
argument. Each mode other than the default shows in pytest's header, so a
flag left from a debugging session cannot change the suite without notice.
pytest prints that header only at normal verbosity, so `-q` hides it.
:::

(reference-data-cleanup)=

### Keep reference data in delete mode

Delete mode empties all tables that `base=` declares. This includes the
tables that your migrations filled with reference data, and nothing puts
those rows back. Name these tables, and Restly leaves them alone:

```python
fr.testing.configure_tests(
    app=app,
    base=Base,
    alembic_upgrade=True,
    db_cleanup="delete",
    db_cleanup_exclude=["country", "role"],
)
```

Every test shares the excluded tables, so a write to one of them stays for
the next test. A name that is not a table raises an error, because a typo
would otherwise empty the table that you wanted to protect. The SaaS example
excludes its `country` table, which a migration fills.

(test-clients)=

## Use the test clients

Both clients check the status code of every response. When the status does
not match, they raise an `AssertionError` that includes the response body.
The `assert_status_code` argument of each request method sets the check:

| `assert_status_code` | Accepted status |
|---|---|
| `"2xx"`, the default | any success status, such as `200`, `201`, or `204` |
| an int, such as `404` | only that status |
| `None` | any status: the client does not check |

Pass the exact code when the status is what the test is about, such as an
error:

```python
def test_not_found(restly_client):
    restly_client.get("/users/999", assert_status_code=404)
```

The default does not check which success status a route returns. To test
that a create returns `201`, pass `assert_status_code=201`.

To test a request as a different user, override the dependency that gives
the user; [Request Context](#current-testing) shows how.

:::{admonition} Details
:class: details

{class}`RestlyTestClient <fastapi_restly.testing.RestlyTestClient>` is the
sync client behind the `restly_client` fixture. The fixture creates it and
enters it for you. Use the client directly when you test without the
fixtures:

```python
from fastapi_restly.testing import RestlyTestClient

with RestlyTestClient(app) as client:
    response = client.get("/users")
```

Use it in a `with` block. Starlette runs the `lifespan` of an application
only inside that block, so a client used outside one skips what `lifespan=`
sets up.

Although it is sync, `RestlyTestClient` also tests async FastAPI routes and
{class}`AsyncRestView <fastapi_restly.views.AsyncRestView>` endpoints.
{class}`AsyncRestlyTestClient <fastapi_restly.testing.AsyncRestlyTestClient>`
has the same request methods and checks for async tests. The
`restly_async_client` fixture creates it and runs the app's lifespan for you.
:::

(writing-async-tests)=

## Write async tests

You probably do not need them. `restly_client` is sync, and it runs async
routes and `AsyncRestView` endpoints well. So you can write the whole suite
of an async application with `def` tests, and usually you should. Async tests
give little here. They need an event loop setting, they give confusing
collection errors when that setting is wrong, and they stop you from querying
the database in `pdb` (see [Look at the database](#inspecting-the-database)).

Write an async test when the test must await something itself:
`restly_async_session` or `fr.open_async_session()` to add rows directly, or
one of your own coroutines. Use `restly_async_client` for HTTP in the same
test.

```python
async def test_direct_setup_and_request(restly_async_client, restly_async_session):
    restly_async_session.add(User(name="Alice"))
    await restly_async_session.commit()

    response = await restly_async_client.get("/users")
    assert response.json()["total_count"] == 1
```

Put pytest-asyncio into
[auto mode](https://pytest-asyncio.readthedocs.io/en/latest/concepts.html#test-discovery-modes),
which collects `async def` tests without a marker on each one:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
```

Without that, or an
[anyio](https://anyio.readthedocs.io/en/stable/testing.html) setup that does
the same, async tests are not collected or give confusing errors. Sync tests
keep working next to them. The
[pytest-asyncio reference](https://pytest-asyncio.readthedocs.io/en/latest/reference/configuration.html)
covers its other settings.

:::{admonition} Details
:class: details

The sync client runs the application on its own event loop, and the async
fixtures run on pytest's loop. So in an application that has only an async
database, a rollback test that uses `restly_client` together with
`restly_async_session` stops while pytest sets up the fixtures, with a
`RestlyConfigurationError`. The error tells you to use
`restly_async_client`, and `fr.open_async_session()` fails with the same
advice when it opens the session. Under `"delete"` and `"none"`, drivers
such as asyncpg, which tie pooled connections to one event loop, need the
same split.
:::

(inspecting-the-database)=

## Look at the database during a test

Under the default mode, `psql` finds no rows while a test runs, and none
after it. The rows are in a transaction that is not committed, on one
connection that no other process can read, and the rollback removes them
when the test ends. There are two ways to see them anyway.

**From inside the test.** `pytest --pdb` stops at the failure while the
transaction is still open, before any fixture is torn down, and
`restly_client` works from there:

```
(Pdb) restly_client.get("/users").json()
{'data': [{'id': 1, 'name': 'Jane'}], 'total_count': 1, ...}
```

A query through your own API like this works in a sync suite and in an async
one, because the client is sync in both. A direct query on a session works
only in a sync suite, with `restly_session.execute(...)`. In an async suite,
`restly_async_session.execute(...)` returns a coroutine that nothing awaits,
and its `sync_session` raises `MissingGreenlet`.

**With ordinary tools.** Run once in [delete mode](#cleaning-up-between-tests):
the rows are committed, and they are still there when the run ends:

```bash
pytest --restly-db-cleanup=delete -k test_the_broken_one
psql myapp_test -c 'select * from "user"'
```

Delete mode cleans before each test, not after, so the rows of the failing
test stay. Use a file database or a server for this: an in-memory database is
gone when pytest exits.

(pytest-fixture-reference)=

## Pytest fixture reference

These are the fixtures that the plugin registers, with their scope and their
exact behavior.

### `restly_app`

**Scope:** `function`

Returns the app that you passed to `configure_tests(app=...)`. Without one, it
returns a bare `FastAPI()`, and every request answers 404. You can also
override this fixture in your `conftest.py` to supply the app.

(restly-client-fixture)=

### `restly_client`

**Scope:** `function`

A [`RestlyTestClient`](#test-clients) for the `restly_app` fixture. In a
configured suite, its requests follow the
[cleanup mode](#cleaning-up-between-tests): the default rolls them back with
the rest of the test, and `"delete"` and `"none"` let them commit. In a suite
that never called `configure_tests()` and uses no session fixture,
**nothing rolls them back**: a test with only the client commits real rows to
the configured database. For backward compatibility, a test that asks for
`restly_session` still runs under rollback isolation, also for its requests
through `restly_client`. `restly_async_session` does the same for requests
through `restly_async_client`.

For an application with only an async database, under rollback, Restly opens
the transaction on the same event loop that the sync client uses to run the
application. For this reason, an asyncpg engine with a normal pool works,
although the test function itself is sync.

### `restly_async_client`

**Scope:** `function`

An [`AsyncRestlyTestClient`](#test-clients) for `restly_app`, with the same
default status checks as `restly_client`. It runs the application's lifespan,
and it uses the same event loop as `restly_async_session` (and, under
rollback, the same transaction). Use it when a test needs both HTTP and
direct async database access.

(restly-session-fixture)=

### `restly_session`

**Scope:** `function`

Under the default rollback mode, a SQLAlchemy `Session` on a pinned
connection whose outer transaction is never committed. Each request during
the test builds its own real session, which joins that transaction through a
savepoint (SQLAlchemy's `create_savepoint` mode). So `commit()` and
`rollback()` work as in production, but nothing stays after the test. The
fixture skips the test if no sync session source is configured.

In a suite that called `configure_tests()`, a custom `sync_session_generator`
follows these rules. The same table applies to the async
`session_generator` and the async sessionmaker.

| Cleanup mode | What happens to the generator |
|---|---|
| `"rollback"` | Restly replaces the generator through `app.dependency_overrides`, and builds isolated sessions from the matching sessionmaker. If only a generator is configured, the setup opens it once to find the bind of its session. Code in the body of the generator, such as `SET search_path`, does not run for test requests. |
| `"delete"` | If either custom generator is configured, pytest stops before the tests run, with a configuration error. This happens even when a matching sessionmaker exists, because Restly cannot check that the generator writes to the database that it would clean. Use the sessionmaker as the session source of the application, or choose `"none"`. |
| `"none"` | Restly does not touch the generator. The session fixture uses the matching sessionmaker if there is one. With only a generator, it skips, because it has no sessionmaker to build the fixture's session from. |

To find the bind, the generator must take no arguments and yield a session
with one bind. If the generator needs request dependencies, chooses binds at
run time, or builds a session that needs other arguments, configure its test
engine or sessionmaker explicitly. Point the generator at a test database
too, because the setup runs its body and its cleanup.

Pass `app=` to
{func}`configure_tests() <fastapi_restly.testing.configure_tests>`, so that
ordinary FastAPI dependencies such as `Depends(get_db)` use the same isolated
request session as Restly. Without the managed setup, use `restly_client`
with `restly_session`, or their async versions. The client installs the
override for the test and removes it after. Your own overrides on the app
stay in place, and they must isolate their own sessions.

Under rollback, `fr.open_session()` uses the same factory as `SessionDep`.
So it also gives a session on the test's pinned connection, even when the
test does not ask for `restly_session`. This makes unit tests that only use
the database easy to write; the fixture only gives you a ready `Session`.

Under a [cleanup mode](#cleaning-up-between-tests) other than `rollback`, the
fixture gives a plain session on the configured database instead, because a
rollback would undo the writes that those modes exist to commit.

You can read a committed write back in the same test:

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

The async version of `restly_session`. To await it in a test body, you
[write an async test](#writing-async-tests). In an async-only project it
needs only `fr.configure(async_database_url=...)`. It skips the test if no
async session source is configured. It treats a configured
`session_generator` (and `fr.open_async_session()`) as the generator table
above says, with the async sessionmaker where the sync fixture uses the sync
one.

In an async-only rollback test, use it with `restly_async_client`, not with
`restly_client`. Then every use of the pinned connection stays on pytest's
event loop.

Under rollback, as on the sync side, `fr.open_async_session()` is isolated
even when the test does not ask for this fixture. Use that form when a unit
test opens several short sessions, or when your application code already
opens its own.

It works like `restly_session`, with `await`:

```python
async def test_user_created(restly_async_session):
    user = User(name="Bob")
    restly_async_session.add(user)
    await restly_async_session.commit()

    result = await restly_async_session.get(User, user.id)
    assert result.name == "Bob"
```

> **Note (rollback only):** When both sync and async session sources are
> configured, Restly runs the async fixture over the test's pinned sync
> connection. The two fixtures then share one transaction, and a write that
> one of them commits is visible to the other in the same test. The async
> driver itself is not used. Under `"delete"` and `"none"`, each fixture uses
> its own configured sessionmaker. There is no shared connection and no outer
> transaction, and a write reaches the other fixture only after a real commit,
> under the normal isolation rules of the database.

### `restly_project_root`

**Scope:** `function`

Goes up from the test file until it finds a `pyproject.toml`, and returns that
directory as a `Path`. Use it to find project files from a test, such as
migration configs or test data. It starts from the test file, not from the
working directory, so it returns the same root wherever you start pytest. In
a monorepo, each test finds the root of its own sub-project.

## Internals

(savepoints-and-rollback)=

### Savepoints and rollback

The default mode follows SQLAlchemy's own recipe for test suites,
[joining a session into an external transaction](https://docs.sqlalchemy.org/en/20/orm/session_transaction.html#session-external-transaction).
For each test, the plugin opens one connection, begins a transaction on it,
and points Restly's session factory at that connection in `create_savepoint`
mode. Every session built during the test joins that transaction through a
savepoint: the sessions that your code opens, and the sessions of the
requests. The public client and session fixtures use this setup; none of them
owns it.

So a `commit()` releases a savepoint, and does not reach the database. A
`rollback()` undoes only the work of its own session. Both work as they do in
production. The outer transaction is never committed: closing the connection
at the end of the test undoes everything at once.

This also makes it fast. The cleanup is one rollback on a connection that is
already open. It costs the same for one row or a thousand, and it does not get
slower as your schema grows.

Explicit transaction blocks also work as in production: `with
restly_session.begin(): ...` and its async form commit on success and roll
back on an error. They stay inside the outer transaction, so nothing remains
after the test.

## See also

- [Request Context](#current-testing): test a request as a different
  user.
- [Database & Configuration](database.md): the session sources that the
  fixtures record.
- {func}`fr.testing.configure_tests() <fastapi_restly.testing.configure_tests>`:
  every argument.
