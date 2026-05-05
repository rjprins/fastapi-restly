# pytest Fixtures Reference

FastAPI-Restly ships a small pytest plugin with namespaced fixtures for test client creation and savepoint-isolated database sessions.

## Setup

Install the testing extra before enabling the plugin:

```bash
pip install "fastapi-restly[testing]"
```

The standard extra also includes the testing dependencies.

After installation, pytest auto-loads the Restly plugin through its `pytest11`
entry point. If your project disables plugin autoloading, register it manually
in `conftest.py`:

```python
pytest_plugins = ["fastapi_restly.pytest_fixtures"]
```

This makes the fixtures below available. Restly does not register autouse fixtures; projects should decide explicitly which global test setup they want.

## Async Tests

Tests that use `restly_async_session` must be run with an async pytest plugin such as `pytest-asyncio` or `anyio`. Configure the asyncio mode in your `pyproject.toml`:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

Without this (or equivalent configuration), async tests will fail to collect or produce confusing errors.

## Fixtures

### `restly_project_root`

**Scope:** `session`

Walks up from `cwd` until it finds a `pyproject.toml`, and returns that directory as a `Path`.

---

### `restly_session`

**Scope:** `function`

Provides a SQLAlchemy `Session` for use in tests. It runs on a connection whose outer transaction is never committed. `restly_session.commit()` is patched to `flush()` + `begin_nested()`, so writes become visible inside the test while the fixture keeps app-level commits inside nested transactions.

```python
def test_user_created(restly_session):
    user = User(name="Alice")
    restly_session.add(user)
    restly_session.commit()

    result = restly_session.get(User, user.id)
    assert result.name == "Alice"
```

Skips automatically if no sync database connection is configured.

---

### `restly_async_session`

**Scope:** `function`

Same as `restly_session` but for async code. In async-only projects it works with just `fr.configure(async_database_url=...)`. If both async and sync sessionmakers are configured, it shares the same underlying connection as `restly_session`, so writes from one are visible to the other within a test.

```python
async def test_user_created(restly_async_session):
    user = User(name="Bob")
    restly_async_session.add(user)
    await restly_async_session.commit()

    result = await restly_async_session.get(User, user.id)
    assert result.name == "Bob"
```

Skips automatically if no async database connection is configured.

> **Note:** `restly_async_session` only shares a DBAPI connection with `restly_session` when both sessionmakers are configured and both engines use the `psycopg` driver (`postgresql+psycopg://`). With other combinations (e.g. `psycopg2` + `asyncpg`), the sessions do not share a connection and will not see each other's writes within the same test.

---

### `restly_app`

**Scope:** `function`

Returns a bare `FastAPI()` instance. Override this fixture in your `conftest.py` to return your actual application:

```python
from myapp.main import app as myapp

@pytest.fixture
def restly_app():
    return myapp
```

---

### `restly_client`

**Scope:** `function`

Returns a `RestlyTestClient` wrapping the `restly_app` fixture. Automatically asserts status codes on each request:

`RestlyTestClient` is intentionally sync-only. It still works for testing async
FastAPI routes and `AsyncRestView` endpoints.

| Method   | Default expected status |
|----------|-------------------------|
| `get`    | `200`                   |
| `post`   | `201`                   |
| `patch`  | `200`                   |
| `delete` | `204`                   |

> **Note:** `put` is available on `RestlyTestClient`. `AsyncRestView` and `RestView` do not generate a `PUT` endpoint by default, but `AsyncReactAdminView` / `ReactAdminView` do (to match `ra-data-simple-rest`). Use `put` against any of those views, or against a custom PUT route you add yourself.

Override the expected code when testing error paths:

```python
def test_not_found(restly_client):
    restly_client.get("/users/999", assert_status_code=404)
```

Pass `assert_status_code=None` to skip assertion and inspect the response yourself.

## Explicit `begin()` caveat

The fixtures patch `commit()` and the session context-manager exit paths so most tests behave as
expected under savepoint isolation. Explicit transaction blocks are also supported:

- `with restly_session.begin(): ...` flushes pending changes when the block exits successfully
- `async with restly_async_session.begin(): ...` does the same for async tests

These fixtures still run under savepoint-based isolation rather than production
transaction management. If a test depends on precise rollback behavior at that
boundary, prefer explicit `flush()` / `rollback()` calls or test against the
public API/client layer instead of depending on fixture internals.

---

## Isolation Model

Both `restly_session` and `restly_async_session` use a layered transaction model so that test data is visible during the test but does not persist afterward:

1. The fixture opens a connection for the test and binds the SQLAlchemy session to that connection.
2. The `restly_session` / `restly_async_session` fixtures patch Restly's configured session factory so code under test receives the same isolated session.
3. The fixtures patch `commit()` to `flush()` + `begin_nested()` — state is visible within the test, and code under test can call `commit()`, but no real commit reaches the database.
4. After the test, the connection is closed without committing, rolling back all changes and restoring the database to its pre-test state.

So both statements are true: savepoints make in-test commits safe and keep request/session code usable, while the final isolation guarantee comes from the outer connection-level transaction never being committed. If a test never calls `commit()`, it may not create an extra nested savepoint, but isolation is still maintained by the outer transaction.

This eliminates per-test teardown code and avoids the cost of recreating the schema between tests.
