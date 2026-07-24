# Test APIs with RestlyTestClient and Fixtures

FastAPI-Restly ships a test client with sensible status-code assertions and a
small pytest plugin with savepoint-isolated database fixtures. This page is a
recipe first and a reference second: set up a working `conftest.py`, write a
first test, then look up exact fixture behavior below.

## Setup

Install the testing extra:

```bash
pip install "fastapi-restly[testing]"
```

The `testing` extra is independent of `standard`, which is runtime-only.

The extra registers a `pytest11` entry point, so pytest auto-loads the Restly
fixtures. If your project disables plugin autoloading, register the plugin
manually in `conftest.py`:

```python
pytest_plugins = ["fastapi_restly.pytest_fixtures"]
```

Restly registers no autouse fixtures; nothing happens until a test requests
one.

## A complete conftest.py

The fixtures isolate tests on whatever database {func}`fr.configure() <fastapi_restly.db.configure>` points at;
they never create the schema. Here is a minimal, copy-paste setup for an
async app:

```python
# conftest.py
import asyncio
from pathlib import Path

import fastapi_restly as fr
import pytest

from myapp.main import app as myapp
from myapp.models import Base

fr.configure(async_database_url="sqlite+aiosqlite:///./test.db")


@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    # Start from a clean file: a leftover test.db (an interrupted run, or an
    # older schema) would seed rows and tables below the per-test transaction
    # that never roll back.
    for leftover in Path().glob("test.db*"):
        leftover.unlink()
    asyncio.run(fr.db.async_create_all(Base))


@pytest.fixture
def restly_app():
    # restly_client wraps whatever this fixture returns.
    return myapp


@pytest.fixture(autouse=True)
async def _isolate_every_test(restly_async_session):
    """Give every test savepoint isolation, client tests included."""
```

The last fixture matters: the savepoint isolation lives in the *session*
fixtures, which swap Restly's session factory for the duration of a test.
`restly_client` alone does not isolate: a client-only test commits real rows
to the configured database. The autouse wrapper opts every test in.

The session fixtures need an async pytest plugin such as `pytest-asyncio`;
configure it in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
```

Without that (or an equivalent `anyio` setup), async tests and fixtures fail
to collect or produce confusing errors.

## A first test

With the conftest in place, a test only needs to request `restly_client`:

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

Everything the test writes (through the client or a session fixture) is
rolled back afterward; see [the isolation model](#isolation-model).

## Test databases and migrations

Point {func}`fr.configure() <fastapi_restly.db.configure>` at a dedicated test database; the fixtures roll back
everything after each test, but schema setup is still your job, once per
session:

- **{func}`async_create_all <fastapi_restly.db.async_create_all>`** (above) builds the schema straight from your models;
  this is fine when migrations are not part of what you are testing.
- **Alembic**: if you want tests to run against the migrated schema, upgrade
  in the same session fixture instead:

  ```python
  from alembic import command
  from alembic.config import Config

  @pytest.fixture(scope="session", autouse=True)
  def _migrate_schema():
      command.upgrade(Config("alembic.ini"), "head")
  ```

See [Migrations with Alembic](deploying.md#migrations-with-alembic) for
production migration setup.

## RestlyTestClient

The `restly_client` fixture wraps this client for you; construct it directly
when you are testing without the fixtures:

```python
from fastapi_restly.testing import RestlyTestClient

client = RestlyTestClient(app)
```

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

## Fixture reference

These are the fixtures the plugin registers, with their scope and exact
behavior.

### `restly_app`

**Scope:** `function`

Returns a bare `FastAPI()` instance. Override it in your `conftest.py` (as in
the recipe above) so `restly_client` wraps your actual application.

### `restly_client`

**Scope:** `function`

A [`RestlyTestClient`](#restlytestclient) wrapping the `restly_app` fixture.
On its own it provides **no database isolation**; pair it with a session
fixture (the conftest recipe's autouse wrapper does this for every test).

### `restly_session`

**Scope:** `function`

A SQLAlchemy `Session` on a pinned connection whose outer transaction is never
committed. Each request during the test builds its own real session that joins
that transaction through a savepoint (SQLAlchemy's `create_savepoint` mode), so
`commit()` and `rollback()` behave as in production while nothing persists past
the test. The fixture skips automatically if no sync session source is
configured at all.

A configured `sync_session_generator` is cleared for the duration of the test
and restored afterwards, so the request builds its own isolated session on the
fixture's connection instead. What is lost is anything the generator body runs
per session, a `SET search_path` for example. Configure a sync sessionmaker for
the tests as well: with only a generator the fixture has nothing to build the
session from, and it raises rather than skip.

`fr.open_session()` resolves the same factory `SessionDep` does, so it too
yields an isolated session on the fixture's connection during a test.

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

The async version of `restly_session`; it requires the [async pytest
setup](#a-complete-conftestpy). In async-only projects it needs only
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

**Scope:** `session`

Walks up from the current working directory until it finds a
`pyproject.toml` and returns that directory as a `Path`. This is a
convenience for locating project files (migration configs, test data) from
tests regardless of where pytest was invoked.

## Isolation model

Both session fixtures use layered transactions: data is visible during the
test and rolled back afterward.

1. The fixture opens a connection for the test and binds the SQLAlchemy
   session to that connection.
2. The fixtures swap Restly's session factory for one bound to that connection
   in `create_savepoint` mode, so code under test builds its own isolated
   session on the shared connection, and clear a configured session generator,
   which the session dependency would otherwise read first. Together these are
   what make app/client requests isolated too, and it is why the conftest recipe
   requests the session fixture for every test.
3. Each session joins the outer transaction through a savepoint, so its
   `commit()` releases the savepoint (state visible on the connection) without a
   real database commit, and its `rollback()` discards only that session's own
   work.
4. After the test, the connection is closed without committing, rolling back
   all changes and restoring the database to its pre-test state.

Savepoints keep in-test commits usable; the uncommitted outer transaction
provides the final isolation. Tests that never call `commit()` are still
isolated, and there is no per-test teardown code or schema rebuild.

Explicit transaction blocks behave as in production: `with restly_session.begin():
...` and `async with restly_async_session.begin(): ...` commit on success and
roll back on error, scoped to the fixture's outer transaction so nothing persists
past the test.
