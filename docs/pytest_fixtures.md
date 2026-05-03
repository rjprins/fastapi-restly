# pytest Fixtures Reference

FastAPI-Restly ships a set of pytest fixtures that handle database setup, transaction isolation, and test client creation.

## Setup

Install the testing extra before enabling the plugin:

```bash
pip install "fastapi-restly[testing]"
```

The standard extra also includes the testing dependencies.

In your `conftest.py`, register the plugin:

```python
pytest_plugins = ["fastapi_restly.pytest_fixtures"]
```

This activates all fixtures below. Autouse fixtures run automatically; the rest you request by name.

## Async Tests

Tests that use `async_session` must be run with an async pytest plugin such as `pytest-asyncio` or `anyio`. Configure the asyncio mode in your `pyproject.toml`:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

Without this (or equivalent configuration), async tests will fail to collect or produce confusing errors.

## Fixtures

### `project_root`

**Scope:** `session`

Walks up from `cwd` until it finds a `pyproject.toml`, and returns that directory as a `Path`. Used internally by `autouse_alembic_upgrade`.

---

### `autouse_alembic_upgrade`

**Scope:** `session` | **Autouse**

Runs `alembic upgrade head` once before the test suite starts. Skips silently if no `alembic/` directory exists in the project root. Calls `pytest.exit()` on migration failure, stopping the suite immediately with the full traceback.

---

### `autouse_savepoint_only_mode_sessions`

**Scope:** `session` | **Autouse**

Calls `activate_savepoint_only_mode()` on both `async_make_session` and `make_session` (whichever are configured). This wraps engine connections in an outer transaction and configures SQLAlchemy's `join_transaction_mode="create_savepoint"`, so sessions opened inside tests join that transaction via savepoints instead of committing directly to the database. Skips if no database connections are configured. See the `Isolation Model` section below.

This fixture runs once for the entire test session and does not deactivate savepoint mode at teardown — the change is permanent for the process lifetime.

---

### `session`

**Scope:** `function`

Provides a SQLAlchemy `Session` for use in tests. It runs on a connection whose outer transaction is never committed. `session.commit()` is patched to `flush()` + `begin_nested()`, so writes become visible inside the test while the fixture keeps app-level commits inside nested transactions.

```python
def test_user_created(session):
    user = User(name="Alice")
    session.add(user)
    session.commit()

    result = session.get(User, user.id)
    assert result.name == "Alice"
```

Skips automatically if no sync database connection is configured.

---

### `async_session`

**Scope:** `function`

Same as `session` but for async code. In async-only projects it works with just `fr.configure(async_database_url=...)`. If both async and sync sessionmakers are configured, it shares the same underlying connection as `session`, so writes from one are visible to the other within a test.

```python
async def test_user_created(async_session):
    user = User(name="Bob")
    async_session.add(user)
    await async_session.commit()

    result = await async_session.get(User, user.id)
    assert result.name == "Bob"
```

Skips automatically if no async database connection is configured.

> **Note:** `async_session` only shares a DBAPI connection with `session` when both sessionmakers are configured and both engines use the `psycopg` driver (`postgresql+psycopg://`). With other combinations (e.g. `psycopg2` + `asyncpg`), the sessions do not share a connection and will not see each other's writes within the same test.

---

### `app`

**Scope:** `function`

Returns a bare `FastAPI()` instance. Override this fixture in your `conftest.py` to return your actual application:

```python
from myapp.main import app as myapp

@pytest.fixture
def app():
    return myapp
```

---

### `client`

**Scope:** `function`

Returns a `RestlyTestClient` wrapping the `app` fixture. Automatically asserts status codes on each request:

| Method   | Default expected status |
|----------|-------------------------|
| `get`    | `200`                   |
| `post`   | `201`                   |
| `patch`  | `200`                   |
| `delete` | `204`                   |

> **Note:** `put` is available on `RestlyTestClient`. `AsyncRestView` and `RestView` do not generate a `PUT` endpoint by default, but `AsyncReactAdminView` / `ReactAdminView` do (to match `ra-data-simple-rest`). Use `put` against any of those views, or against a custom PUT route you add yourself.

Override the expected code when testing error paths:

```python
def test_not_found(client):
    client.get("/users/999", assert_status_code=404)
```

Pass `assert_status_code=None` to skip assertion and inspect the response yourself.

## Explicit `begin()` caveat

The fixtures patch `commit()` and the session context-manager exit paths so most tests behave as
expected under savepoint isolation. There is still a documented caveat around explicit transaction
blocks:

- `with session.begin(): ...` and `async with session.begin(): ...` are supported
- The fixture implementation notes that the `begin().__exit__` / `begin().__aexit__` path does not
  currently mirror production perfectly for visibility after the block exits

If your tests depend on precise behavior at that boundary, prefer explicit `flush()` calls or test
against the public API/client layer instead of depending on fixture internals.

---

## Isolation Model

Both `session` and `async_session` use a layered transaction model so that test data is visible during the test but does not persist afterward:

1. `activate_savepoint_only_mode()` wraps engine connections so a real database connection starts an outer transaction before a session uses it.
2. The session factory is configured with `join_transaction_mode="create_savepoint"`, so sessions that join that connection work inside savepoints rather than committing the outer transaction.
3. The `session` / `async_session` fixtures patch `commit()` to `flush()` + `begin_nested()` — state is visible within the test, and code under test can call `commit()`, but no real commit reaches the database.
4. After the test, the connection is closed without committing the outer transaction, rolling back all changes and restoring the database to its pre-test state.

So both statements are true: savepoints make in-test commits safe and keep request/session code usable, while the final isolation guarantee comes from the outer connection-level transaction never being committed. If a test never calls `commit()`, it may not create an extra nested savepoint, but isolation is still maintained by the outer transaction.

This eliminates per-test teardown code and avoids the cost of recreating the schema between tests.
