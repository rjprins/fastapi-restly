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

This makes the fixtures below available. Restly does not register autouse fixtures; opt in from your project.

## Async Tests

Tests using `restly_async_session` need an async pytest plugin such as `pytest-asyncio` or `anyio`. Configure asyncio mode in `pyproject.toml`:

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

Provides a SQLAlchemy `Session` on a connection whose outer transaction is never committed. `commit()` is patched to `flush()` + `begin_nested()`, so writes are visible during the test without persisting afterward.

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

Async version of `restly_session`. In async-only projects it needs only `fr.configure(async_database_url=...)`. If both async and sync sessionmakers are configured, both fixtures share a connection and see each other's writes.

```python
async def test_user_created(restly_async_session):
    user = User(name="Bob")
    restly_async_session.add(user)
    await restly_async_session.commit()

    result = await restly_async_session.get(User, user.id)
    assert result.name == "Bob"
```

Skips automatically if no async database connection is configured.

> **Note:** `restly_async_session` shares a DBAPI connection with `restly_session` only when both sessionmakers are configured and both engines use `psycopg` (`postgresql+psycopg://`). Other driver combinations, such as `psycopg2` + `asyncpg`, do not share writes inside one test.

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

> **Note:** `put` is available on `RestlyTestClient`. `AsyncRestView` and `RestView` do not generate `PUT`; React Admin views and custom routes may.

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

These fixtures still run under savepoint-based isolation, not production
transaction management. If a test depends on precise rollback behavior at that
boundary, prefer explicit `flush()` / `rollback()` calls or test against the
public API/client layer instead of fixture internals.

---

## Isolation Model

Both session fixtures use layered transactions: data is visible during the test and rolled back afterward.

1. The fixture opens a connection for the test and binds the SQLAlchemy session to that connection.
2. The fixtures patch Restly's session factory so code under test receives the same isolated session.
3. The fixtures patch `commit()` to `flush()` + `begin_nested()`, making state visible without a real database commit.
4. After the test, the connection is closed without committing, rolling back all changes and restoring the database to its pre-test state.

Savepoints keep in-test commits usable; the uncommitted outer transaction provides the final isolation. Tests that never call `commit()` are still isolated.

This eliminates per-test teardown code and avoids the cost of recreating the schema between tests.
