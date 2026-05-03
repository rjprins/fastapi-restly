# FastAPI-Restly

[![CI](https://github.com/rjprins/fastapi-restly/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/rjprins/fastapi-restly/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue)](https://github.com/rjprins/fastapi-restly/blob/main/pyproject.toml)
[![License](https://img.shields.io/github/license/rjprins/fastapi-restly)](https://github.com/rjprins/fastapi-restly/blob/main/LICENSE)
[![Coverage](https://rjprins.github.io/fastapi-restly/coverage/badge.svg)](https://rjprins.github.io/fastapi-restly/coverage/)

<p align="center">
  <img src="docs/_static/restly-cat.png" alt="FastAPI-Restly logo" width="200">
</p>

**Build maintainable CRUD APIs on FastAPI, SQLAlchemy 2.0, and Pydantic v2 — with real class-based views.**

> **Status:** `3.0.0` — first public release. See the [Changelog](CHANGELOG.md). Not yet on PyPI:
> ```bash
> pip install git+https://github.com/rjprins/fastapi-restly.git
> ```

**Docs:** <https://rjprins.github.io/fastapi-restly/> · **[Changelog](CHANGELOG.md)** · **[Contributing](CONTRIBUTING.md)** · **[Security](SECURITY.md)** · **[Code of Conduct](CODE_OF_CONDUCT.md)** · **[Examples](example-projects/)**

## Why FastAPI-Restly?

The differentiator is **true class-based views**. You subclass `RestView` / `AsyncRestView` and override handlers like `handle_get`, `handle_create`, or object helpers like `save_object` — CRUD logic is *methods you can swap*, not opaque generated functions. Share behavior across views via inheritance the way you would with any Python class.

* **CRUD endpoints in minutes** — auto-generates Pydantic schemas from your SQLAlchemy models.
* **Override anything** — replace an endpoint, or just its business logic, without awkward hacks.
* **React Admin ready** — `AsyncReactAdminView` speaks the `ra-data-simple-rest` wire contract, no custom data provider needed.
* **Modern stack** — SQLAlchemy 2.0, Pydantic v2, async and sync support.
* **Filtering, pagination, sorting** — JSONAPI-style and standard HTTP query interfaces.
* **Field control** — `ReadOnly` / `WriteOnly` markers, plus scalar foreign-key references via `IDRef[...]`.
* **Testing utilities** — `RestlyTestClient` and savepoint-based isolation fixtures.

## Quickstart

```python
import fastapi_restly as fr
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import Mapped

fr.configure(async_database_url="sqlite+aiosqlite:///app.db")
app = FastAPI()

# Define your model — IDBase adds an auto-incrementing integer id.
# Use IDStampsBase to also get created_at / updated_at timestamps.
class User(fr.IDBase):
    name: Mapped[str]
    email: Mapped[str]
    age: Mapped[int]

# Create tables — for demo purposes; use Alembic migrations in production.
# Must run AFTER model declaration so the table is registered on the metadata.
fr.DataclassBase.metadata.create_all(create_engine("sqlite:///app.db"))

# Create CRUD endpoints with auto-generated Pydantic schemas.
# Use RestView instead of AsyncRestView for sync SQLAlchemy.
@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User


# That's it! You now have a fully functional API with:
# - GET    /users/      — list with filtering, sorting, pagination
# - POST   /users/      — create
# - GET    /users/{id}  — read one
# - PATCH  /users/{id}  — partial update
# - DELETE /users/{id}  — delete
```

Run it with any ASGI server (`uv add uvicorn`):

```bash
uvicorn main:app --reload
```

Then open <http://127.0.0.1:8000/docs> for the interactive Swagger UI.

## How does it compare?

[`fastapi-crudrouter`](https://github.com/awtkns/fastapi-crudrouter) and [`fastcrud`](https://github.com/igorbenav/fastcrud) generate CRUD **functions** and register them on a router. FastAPI-Restly generates CRUD **methods on a class you can subclass**.

| | fastapi-crudrouter | fastcrud | **fastapi-restly** |
|---|---|---|---|
| Style | Router factory | Router factory | **Class-based views** |
| Customize an endpoint | Replace the route | Replace the route | Override `handle_get` / `handle_create` / `save_object`, or replace the route |
| Share behavior across resources | Wrapper functions | Wrapper functions | **Subclass a base view** |
| Schema generation | Optional | Optional | Optional (auto from model) |
| SQLAlchemy 2.0 / Pydantic v2 | Partial | Yes | **Yes, native** |
| React Admin wire contract | No | No | **Built-in (`AsyncReactAdminView`)** |

If you want a router that drops in and disappears, the CRUD-router libraries are a good fit. If you want a small object-oriented layer where every operation is a hookable method, that's Restly.

## Philosophy

Restly is a stack of micro-libraries. Each layer adds convenience while letting you drop down for deeper control. The less customization you need, the more you get out-of-the-box — full customization never requires awkward hacks. Restly stays close to patterns already provided by FastAPI, Pydantic, and SQLAlchemy.

## Installation (development)

```bash
git clone https://github.com/rjprins/fastapi-restly.git
cd fastapi-restly
uv sync
```

### Typing compatibility

Restly keeps consumer-facing typing fixtures in [`tests/typing/`](tests/typing) checked with Pyright to catch editor regressions:

```bash
make test-typing
```

## Advanced features

### Manual schema definition

For custom validation or field aliases:

```python
class UserSchema(fr.IDSchema):
    name: str
    email: str
    age: int
    internal_id: fr.ReadOnly[str]

@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserSchema
```

Use **auto-schema** for prototypes and internal tools. Use an **explicit schema** when contract stability and validation control matter (public APIs, aliases, strict response shapes).

### Query modifiers

Two interfaces:

**V1 (JSONAPI-style):**
```bash
GET /users/?filter[name]=John&filter[age]=>21
GET /users/?sort=name,-created_at
GET /users/?limit=10&offset=20
GET /users/?contains[name]=john
```

**V2 (standard HTTP):**
```bash
GET /users/?name=John&email__contains=example
GET /users/?order_by=name,-created_at
GET /users/?page=2&page_size=10
```

Notes:
- V1 uses schema field names, not aliases.
- V2 uses schema aliases for flat fields. With `populate_by_name=True`, flat fields also accept the Python field name.
- V2 relation filters keep the relation segment as the schema/model field name and only use aliases for nested fields. Example: `author.authorName=Alice`, not `writer.authorName=Alice`.

### Read-only and write-only fields

`IDSchema` already provides a read-only `id`, so don't redeclare it unless you need to narrow the type.

```python
class UserSchema(fr.IDSchema):
    name: str
    email: str
    password: fr.WriteOnly[str]        # never appears in responses
    created_at: fr.ReadOnly[datetime]  # cannot be set in requests
```

### Relationships

```python
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

class Order(fr.IDBase):
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"))
    total: Mapped[float]

class OrderSchema(fr.IDSchema):
    customer: CustomerSchema             # nested object
    customer_id: fr.IDRef[Customer]      # wire format: 123 — resolved to FK
    total: float
```

### Custom endpoints and handlers

Add endpoints with `@fr.get`, `@fr.post`, `@fr.put`, `@fr.patch`, `@fr.delete`, or the generic `@fr.route`. Override `handle_*` handlers (`handle_list`, `handle_get`, `handle_create`, ...) to customise built-in CRUD logic without replacing the endpoint.

```python
@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserSchema

    @fr.get("/{id}/download")
    async def download_user(self, id: int):
        return {"id": id, "status": "ok"}

    async def handle_list(self, query_params, query=None):
        # Custom logic here
        return await super().handle_list(query_params, query=query)
```

### React Admin integration

Use `AsyncReactAdminView` to get a backend that [react-admin](https://marmelab.com/react-admin/) with [`ra-data-simple-rest`](https://github.com/marmelab/react-admin/tree/master/packages/ra-data-simple-rest) connects to out of the box:

```python
@fr.include_view(app)
class ProductView(fr.AsyncReactAdminView):
    prefix = "/products"
    model = Product
    schema = ProductSchema
```

The view speaks the `ra-data-simple-rest` wire contract:

- **List** — translates `sort=["name","ASC"]`, `range=[0,24]`, and `filter={"name":"foo"}` into SQL and returns a JSON array with a `Content-Range: items 0-24/315` header.
- **All other CRUD** — `GET /{id}`, `POST /`, `PATCH /{id}`, `DELETE /{id}` work unchanged.

See [React Admin Integration](https://rjprins.github.io/fastapi-restly/howto_react_admin.html) in the docs for CORS setup and customization.

### Excluding built-in routes

```python
@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    exclude_routes = ("delete",)  # names: "index", "get", "post", "patch", "delete"
```

### Pagination metadata

```python
@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    include_pagination_metadata = True
    # Response: {"items": [...], "total": N, "page": 1, "page_size": 100, "total_pages": N, ...}
```

## Testing

`fastapi_restly.testing` provides pytest fixtures (`app`, `client`, `async_session`, `session`) with **savepoint-based isolation** — each test runs inside a transaction that rolls back automatically, so no data leaks between tests. Add to your `conftest.py`:

```python
# conftest.py
import fastapi_restly as fr

pytest_plugins = ["fastapi_restly.pytest_fixtures"]

fr.configure(async_database_url="sqlite+aiosqlite:///test.db")
```

`RestlyTestClient` automatically asserts the expected HTTP status (`200` for GET, `201` for POST, `204` for DELETE, ...) and raises a descriptive `AssertionError` with the response body on failure:

```python
# test_users.py
def test_create_and_fetch_user(client):
    # Raises AssertionError if status != 201
    response = client.post("/users/", json={"name": "John", "email": "john@example.com"})
    user_id = response.json()["id"]

    # Raises AssertionError if status != 200
    data = client.get(f"/users/{user_id}").json()
    assert data["name"] == "John"
```

Pass `assert_status_code=None` to skip the assertion and inspect the response yourself.

## Configuration

```python
# Async SQLite
fr.configure(async_database_url="sqlite+aiosqlite:///app.db")

# Async PostgreSQL
fr.configure(async_database_url="postgresql+asyncpg://user:pass@localhost/db")

# Sync SQLite
fr.configure(database_url="sqlite:///app.db")
```

## Documentation

- **[Getting Started](https://rjprins.github.io/fastapi-restly/getting_started.html)** — fast path from zero to a working API
- **[User Guide](https://rjprins.github.io/fastapi-restly/user_guide.html)** — tutorial walkthroughs and topic guides
- **[API Reference](https://rjprins.github.io/fastapi-restly/api_reference.html)** — complete API docs

## Examples

Complete applications under [`example-projects/`](example-projects/):

- **[Shop](example-projects/shop/)** — e-commerce API with products, orders, customers
- **[Blog](example-projects/blog/)** — minimal blog with a single `Blog` model
- **[SaaS](example-projects/saas/)** — multi-tenant project management API

## Contributing

Pull requests and issue discussions welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, coding standards, and the test workflow. By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md). Security issues: see [SECURITY.md](SECURITY.md).

## License

MIT — see [LICENSE](LICENSE).
