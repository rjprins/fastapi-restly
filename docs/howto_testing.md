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
- `patch` expects `200`
- `delete` expects `204`

Override when needed:

```python
client.patch("/users/999", json={}, assert_status_code=404)
```

## Pytest Fixtures

```python
pytest_plugins = ["fastapi_restly.testing._fixtures"]
```

Useful fixtures include:
- `app`
- `client`
- `session`
- `async_session`

## Run Test Suites

```bash
make test-framework
make test-examples
make test-all
```

