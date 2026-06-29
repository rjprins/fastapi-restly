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

The fixtures isolate tests on whatever database {func}`fr.configure() <fastapi_restly.db.configure>` points at —
they never create the schema. A minimal, copy-paste setup for an async app:

```python
# conftest.py
import asyncio

import fastapi_restly as fr
import pytest

from myapp.main import app as myapp
from myapp.models import Base

fr.configure(async_database_url="sqlite+aiosqlite:///./test.db")


@pytest.fixture(scope="session", autouse=True)
def _create_schema():
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
fixtures, which patch Restly's session factory for the duration of a test.
`restly_client` alone does not isolate — a client-only test commits real rows
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

With the conftest above, everything the test writes — through the client or a
session fixture — is rolled back afterward; see
[the isolation model](#isolation-model).

## Test databases and migrations

Point {func}`fr.configure() <fastapi_restly.db.configure>` at a dedicated test database; the fixtures roll back
everything after each test, but schema setup is still your job, once per
session:

- **{func}`create_all <fastapi_restly.db.create_all>`** (above) builds the schema straight from your models — fine
  when migrations aren't part of what you're testing.
- **Alembic** — if you want tests to run against the migrated schema, upgrade
  in the same session fixture instead:

  ```python
  from alembic import command
  from alembic.config import Config

  @pytest.fixture(scope="session", autouse=True)
  def _migrate_schema():
      command.upgrade(Config("alembic.ini"), "head")
  ```

See [Deploying](deploying.md) for production migration setup.

## RestlyTestClient

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
`put` exists for React Admin views and custom routes.

Override the expectation when testing error paths:

```python
def test_not_found(restly_client):
    restly_client.get("/users/999", assert_status_code=404)
```

Passing `assert_status_code=None` relaxes the check to "any status below
400" — it does **not** skip the assertion. To inspect an error response
yourself, pass the error code you expect.

## Fixture reference

### `restly_app`

**Scope:** `function`

Returns a bare `FastAPI()` instance. Override it in your `conftest.py` (as in
the recipe above) so `restly_client` wraps your actual application.

### `restly_client`

**Scope:** `function`

A [`RestlyTestClient`](#restlytestclient) wrapping the `restly_app` fixture.
On its own it provides **no database isolation** — pair it with a session
fixture (the conftest recipe's autouse wrapper does this for every test).

### `restly_session`

**Scope:** `function`

A SQLAlchemy `Session` on a connection whose outer transaction is never
committed. `commit()` is patched to `flush()` + `begin_nested()`, so writes
are visible during the test without persisting afterward. Skips automatically
if no sync database connection is configured.

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

Async version of `restly_session`; requires the [async pytest
setup](#a-complete-conftestpy). In async-only projects it needs only
`fr.configure(async_database_url=...)`. Skips automatically if no async
database connection is configured.

```python
async def test_user_created(restly_async_session):
    user = User(name="Bob")
    restly_async_session.add(user)
    await restly_async_session.commit()

    result = await restly_async_session.get(User, user.id)
    assert result.name == "Bob"
```

> **Note:** `restly_async_session` shares a DBAPI connection with
> `restly_session` only when both sessionmakers are configured and both
> engines use `psycopg` (`postgresql+psycopg://`). Other driver combinations,
> such as `psycopg2` + `asyncpg`, do not share writes inside one test.

### `restly_project_root`

**Scope:** `session`

Walks up from the current working directory until it finds a
`pyproject.toml` and returns that directory as a `Path` — a convenience for
locating project files (migration configs, test data) from tests regardless
of where pytest was invoked.

## Isolation model

Both session fixtures use layered transactions: data is visible during the
test and rolled back afterward.

1. The fixture opens a connection for the test and binds the SQLAlchemy
   session to that connection.
2. The fixtures patch Restly's session factory so code under test receives
   the same isolated session. This patch is what makes app/client requests
   isolated too — which is why the conftest recipe requests the session
   fixture for every test.
3. The fixtures patch `commit()` to `flush()` + `begin_nested()`, making
   state visible without a real database commit.
4. After the test, the connection is closed without committing, rolling back
   all changes and restoring the database to its pre-test state.

Savepoints keep in-test commits usable; the uncommitted outer transaction
provides the final isolation. Tests that never call `commit()` are still
isolated, and there is no per-test teardown code or schema rebuild.

Explicit transaction blocks are supported — `with restly_session.begin(): ...`
and `async with restly_async_session.begin(): ...` flush pending changes when
the block exits successfully. The fixtures still run under savepoint-based
isolation, not production transaction management: if a test depends on precise
rollback behavior at that boundary, prefer explicit `flush()` / `rollback()`
calls, or test through the client instead of fixture internals.
