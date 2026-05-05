# How-To: Test APIs with RestlyTestClient and Fixtures

FastAPI-Restly includes a test client and pytest fixtures for fast endpoint tests.

Install the testing extra before importing these helpers:

```bash
pip install "fastapi-restly[testing]"
```

The standard extra also includes the testing dependencies.

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

- `restly_app` — returns a bare `FastAPI()` instance. **You must override this in your own `conftest.py`** to return your actual application, otherwise the `restly_client` fixture wraps an empty app with no routes.
- `restly_client` — a `RestlyTestClient` wrapping the `restly_app` fixture.
- `restly_session` — a SQLAlchemy `Session` with savepoint-based isolation.
- `restly_async_session` — same as `restly_session` but for async code.

Restly does not register autouse fixtures. If you want database isolation for every test, opt in from your own `conftest.py` by requesting the fixture there.

One caveat to be aware of: explicit `with restly_session.begin(): ...` / `async with restly_async_session.begin(): ...`
blocks inside tests are supported, but the fixture implementation currently documents a visibility
caveat around those blocks. See [pytest Fixtures Reference](pytest_fixtures.md) for details.

Example — override `restly_app` in your `conftest.py`:

```python
import pytest
from myapp.main import app as myapp

@pytest.fixture
def restly_app():
    return myapp
```

See [pytest Fixtures Reference](pytest_fixtures.md) for the full fixture list and isolation model details.

## Run Test Suites

```bash
make test-framework
make test-examples
make test-all
```
