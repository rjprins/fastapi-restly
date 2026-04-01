# How-To: Test APIs with RestlyTestClient and Fixtures

FastAPI-Restly includes a test client and pytest fixtures for fast endpoint tests.

## Client

```python
from fastapi_restly.testing import RestlyTestClient

client = RestlyTestClient(app)
```

The client auto-asserts default status codes:
- `get` expects `200`
- `post` expects `201`
- `put` expects `200`
- `patch` expects `200`
- `delete` expects `204`

Override when needed:

```python
client.patch("/users/999", json={}, assert_status_code=404)
```

Pass `assert_status_code=None` to skip the assertion entirely and inspect the response yourself.

## Pytest Fixtures

```python
pytest_plugins = ["fastapi_restly.pytest_fixtures"]
```

Useful fixtures include:

- `app` — returns a bare `FastAPI()` instance. **You must override this in your own `conftest.py`** to return your actual application, otherwise the `client` fixture wraps an empty app with no routes.
- `client` — a `RestlyTestClient` wrapping the `app` fixture.
- `session` — a SQLAlchemy `Session` with savepoint-based isolation.
- `async_session` — same as `session` but for async code.

Two fixtures run automatically for every test session (you do not need to request them):

- `autouse_alembic_upgrade` — runs `alembic upgrade head` once before the suite starts. If migrations fail, the entire suite is aborted immediately. Skips silently if no `alembic/` directory is found.
- `autouse_savepoint_only_mode_sessions` — puts session factories into savepoint-only mode so no test data is committed to the database. Skips if no database connections are configured.

One caveat to be aware of: explicit `with session.begin(): ...` / `async with session.begin(): ...`
blocks inside tests are supported, but the fixture implementation currently documents a visibility
caveat around those blocks. See [pytest Fixtures Reference](pytest_fixtures.md) for details.

A third fixture, `project_root`, is session-scoped and used internally by `autouse_alembic_upgrade` to locate the project directory. It is not autouse.

Example — override `app` in your `conftest.py`:

```python
import pytest
from myapp.main import app as myapp

@pytest.fixture
def app():
    return myapp
```

See [pytest Fixtures Reference](pytest_fixtures.md) for the full fixture list and isolation model details.

## Run Test Suites

```bash
make test-framework
make test-examples
make test-all
```
