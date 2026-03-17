# pytest Fixtures Reference

FastAPI-Restly ships a set of pytest fixtures that handle database setup, transaction isolation, and test client creation.

## Setup

In your `conftest.py`, register the plugin:

```python
pytest_plugins = ["fastapi_restly.pytest_fixtures"]
```

This activates all fixtures below. Autouse fixtures run automatically; the rest you request by name.

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

Calls `activate_savepoint_only_mode()` on both `async_make_session` and `make_session` (whichever are configured). This puts the session factories into a mode where transactions are never fully committed to the database. Skips if no database connections are configured. See the `Isolation Model` section below.

---

### `session`

**Scope:** `function`

Provides a SQLAlchemy `Session` for use in tests. Each test runs inside a savepoint; `session.commit()` is patched to `flush()` + `begin_nested()`, so writes are visible within the test but rolled back after it ends.

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

Same as `session` but for async code. In async-only projects it works with just `setup_async_database_connection(...)`. If both async and sync sessionmakers are configured, it shares the same underlying connection as `session`, so writes from one are visible to the other within a test.

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
| `put`    | `200`                   |
| `patch`  | `200`                   |
| `delete` | `204`                   |

Override the expected code when testing error paths:

```python
def test_not_found(client):
    client.get("/users/999", assert_status_code=404)
```

---

## Isolation Model

Both `session` and `async_session` use savepoint-based isolation so that no test data persists between tests:

1. A real database transaction is opened at the start of the test (never committed).
2. A savepoint is established before test code runs.
3. Inside the test, `commit()` is patched to `flush()` + `begin_nested()` — state is visible within the test but no real commit happens.
4. After the test, the transaction is rolled back, restoring the database to its pre-test state.

This eliminates per-test teardown code and avoids the cost of recreating the schema between tests.
