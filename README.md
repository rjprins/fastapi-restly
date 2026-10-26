<p align="center">
  <img src="https://raw.githubusercontent.com/rjprins/fastapi-restly/main/docs/_static/fr-wordmark.svg" alt="FastAPI-Restly" width="664">
</p>

<p align="center">
  <a href="https://pypi.org/project/fastapi-restly/"><img src="https://img.shields.io/pypi/v/fastapi-restly?color=009b84" alt="PyPI"></a>
  <a href="https://github.com/rjprins/fastapi-restly/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/rjprins/fastapi-restly/ci.yml?branch=main&amp;label=CI&amp;color=009b84" alt="CI"></a>
  <a href="https://github.com/rjprins/fastapi-restly/blob/main/pyproject.toml"><img src="https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-009b84" alt="Python"></a>
  <a href="https://github.com/rjprins/fastapi-restly/blob/main/LICENSE"><img src="https://img.shields.io/github/license/rjprins/fastapi-restly?color=009b84" alt="License"></a>
  <a href="https://www.fastapi-restly.org/coverage/"><img src="https://img.shields.io/endpoint?url=https%3A%2F%2Fwww.fastapi-restly.org%2Fcoverage%2Fsummary.json&amp;color=009b84" alt="Coverage"></a>
</p>

<p align="center">
  <strong>Class-based REST resources for FastAPI and SQLAlchemy 2.0. Write each rule once.</strong>
</p>

Restly turns a SQLAlchemy model into a REST resource: the five CRUD routes,
their Pydantic schemas, and filtering, sorting and pagination on the list
route. Each route runs a fixed list of steps. To change one step, you
override its method, and the other steps stay the same. So you write each
rule once, even when your API is more than plain CRUD.

Restly is a toolkit. It works inside the FastAPI app that you already have.
Add it one resource at a time, and use only the parts that you want.

With FastAPI-Restly imported as `fr`, a CRUD resource is four lines when `app`
and `User` exist:

```python
@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User  # SQLAlchemy model
```

`UserView` inherits five CRUD endpoint methods from `AsyncRestView`.
`include_view` registers them under `/users`.

> **Status:** beta. Restly ran in production for four years before it became
> public. Until 1.0, a minor release can have breaking changes, and the
> [changelog](https://github.com/rjprins/fastapi-restly/blob/main/CHANGELOG.md)
> says what to change in your code. Subscribe to the
> [newsletter](https://www.fastapi-restly.org/newsletter.html) to get an email
> when 1.0 is released. If something is broken or missing, please
> [open an issue](https://github.com/rjprins/fastapi-restly/issues).

Install Restly and `aiosqlite`, the async SQLite driver that the Quickstart
uses:

```bash
pip install "fastapi-restly[standard]" aiosqlite
```

[Docs](https://www.fastapi-restly.org/) ·
[Changelog](https://github.com/rjprins/fastapi-restly/blob/main/CHANGELOG.md) ·
[Examples](https://github.com/rjprins/fastapi-restly/tree/main/example-projects) ·
[Newsletter](https://www.fastapi-restly.org/newsletter.html) ·
[Contributing](https://github.com/rjprins/fastapi-restly/blob/main/CONTRIBUTING.md) ·
[Security](https://github.com/rjprins/fastapi-restly/blob/main/SECURITY.md)

## Quickstart

This example is complete. Save it as `main.py`, run `fastapi dev main.py`,
and open <http://127.0.0.1:8000/docs>. It creates the SQLite tables at
startup, for local development. Use Alembic migrations in production.

```python
from contextlib import asynccontextmanager

import fastapi_restly as fr
from fastapi import FastAPI
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

fr.configure(async_database_url="sqlite+aiosqlite:///app.db")

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    email: Mapped[str]
    active: Mapped[bool] = mapped_column(default=True)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    await fr.db.async_create_all(Base)  # dev tables; use Alembic in production
    yield

app = FastAPI(lifespan=lifespan)

@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
```

That view has these routes:

```http
GET    /users       # list users, with filtering, sorting, and pagination
POST   /users       # create a user
GET    /users/{id}  # read one user
PATCH  /users/{id}  # partially update one user
DELETE /users/{id}  # delete one user
```

Restly generates the Pydantic schemas from the model. The list returns a page
of at most 50 rows, in an envelope:
`{"data": [...], "total_count": 2, "page": 1, "page_size": 50, "total_pages": 1}`.

The example uses plain SQLAlchemy, and the `Base` stays yours. In a real
project, add `fr.ModelDefaults` to the base: models become dataclasses, and
each table gets its name from the class. Then `fr.IDMixin` takes the place of
the `id` column, and `fr.IDSchema` gives a schema its `id`. The examples below
use this base and model:

```python
class Base(fr.ModelDefaults, DeclarativeBase):
    pass

class User(fr.IDMixin, Base):
    name: Mapped[str]
    email: Mapped[str]
    active: Mapped[bool] = mapped_column(default=True)
```

## What Restly does differently

### Change one step, keep the rest

Each CRUD route runs a fixed list of steps. To change one step, override its
method. This view overrides `delete`, so that it keeps the row and marks the
user as inactive:

```python
@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User

    async def delete(self, user):
        user.active = False  # keep the row
```

`DELETE /users/{id}` still runs `authorize`, commits and returns 204. The
other four routes do not change. See
[Request Lifecycle & Hooks](https://www.fastapi-restly.org/lifecycle.html).

### A hidden row is a 404, a refused action is a 403

A scope on the model decides which rows exist for a request. Restly applies
it when a view lists, reads, updates or deletes rows of the model, and in
every reference check. `authorize` decides what the caller may do with a row
that they can see.

```python
class PostClauses(fr.ClauseNamespace):
    model = Post

    default_scope = fr.where_clause(  # published posts, and your own drafts
        or_(Post.published, Post.author_id == Current.user_id)
    )


class PostView(fr.AsyncRestView):
    ...

    async def authorize(self, action, obj=None, data=None):
        if action in (fr.Action.UPDATE, fr.Action.DELETE):
            if obj.author_id != Current.user_id():
                raise fr.exc.Forbidden("Only the author can change this")
```

`Current.user_id()` is the id of the caller. Your app sets it for each
request; see [Request Context](https://www.fastapi-restly.org/context.html).
The scope uses `Current.user_id` without a call, so that Restly reads the
value each time it applies the scope. Another user's draft is a 404 on `GET`,
`PATCH` and `DELETE`, and the list does not show or count it. See
[Scopes](https://www.fastapi-restly.org/scopes.html) and
[Authorization](https://www.fastapi-restly.org/authorization.html).

### One schema for requests and responses

Write one Pydantic schema for the resource. `fr.ReadOnly` marks a field that
only goes out, and `fr.WriteOnly` marks a field that only comes in. Restly
builds the response class and the two request bodies from it, with names that
OpenAPI and generated clients show. Here the `User` model also has
`password`, `team_id` and `created_at` columns:

```python
class UserSchema(fr.IDSchema):
    name: str
    email: str
    password: fr.WriteOnly[str]
    team_id: fr.MustExist[int, Team]
    created_at: fr.ReadOnly[datetime]
```

| Class | Used for | Fields |
|---|---|---|
| `UserResponse` | every response | `id`, `name`, `email`, `team_id`, `created_at` |
| `UserCreate` | the body of `POST` | `name`, `email`, `password`, `team_id` |
| `UserUpdate` | the body of `PATCH` | the same as `UserCreate`, and the client can leave out any of them |

`team_id` is a reference: Restly checks that the team exists, inside the
scope of `Team`. A wrong id is a 404. To make a body or the response
different, write that schema yourself and set it on the view. See
[Schemas & Validation](https://www.fastapi-restly.org/schemas.html).

### SQLAlchemy stays SQLAlchemy, FastAPI stays FastAPI

Your declarative base, your engine and your app stay yours. Add Restly next
to the routes that you have, one resource at a time. When one route needs
something that Restly does not do, write it as a plain FastAPI route.

```python
app = FastAPI()
app.include_router(orders_router)  # your routes stay as they are
fr.configure(async_engine=engine)  # Restly uses your engine

@fr.include_view(app)
class CustomerView(fr.AsyncRestView):
    prefix = "/customers"
    model = Customer  # a model on your own Base
```

See [New & Existing Projects](https://www.fastapi-restly.org/projects.html).

## Not just CRUD

`fr.View` is a view without the CRUD routes. Use it for a group of
endpoints that are not CRUD, such as login, webhooks or actions:

```python
@fr.include_view(app)
class AuthView(fr.View):
    prefix = "/auth"
    tags = ["auth"]
    session: fr.AsyncSessionDep

    @fr.post("/login")
    async def login(self, credentials: LoginRequest) -> Token: ...
```

Views are Python classes, so they share code by inheritance. Declare the
dependencies of your app once, on a base view, and read them from `self` in
every view:

```python
class AppView(fr.View):
    session: fr.AsyncSessionDep
    current_user: Annotated[User, Depends(get_current_user)]

class ProfileView(AppView): ...                    # custom endpoints
class AppRestView(AppView, fr.AsyncRestView): ...  # CRUD resources, same dependencies
```

Use a plain FastAPI route for one endpoint, `View` for a group of custom
endpoints, and `AsyncRestView` or `RestView` for a CRUD resource. A CRUD view
can have custom routes too, with `@fr.post` and the other route decorators.
See [Views](https://www.fastapi-restly.org/views.html).

## Also in Restly

- List params: `GET /users?name=John&sort=-id&page=2`, and operators such as
  `email__icontains=example.com`. The keys come from the response class, and
  an unknown key is a 422. See
  [Filtering, Sorting & Pagination](https://www.fastapi-restly.org/filtering.html).
- Tests: `pip install "fastapi-restly[testing]"` adds pytest fixtures. Each
  test runs in a transaction that rolls back, so a test needs no cleanup code.
  See
  [Testing](https://www.fastapi-restly.org/testing.html).
- A new project: `restly new myapp` writes a complete project, with a `users`
  resource and its tests. See
  [New & Existing Projects](https://www.fastapi-restly.org/projects.html).
- Database conflicts: a duplicate unique value in a CRUD write or a
  `write_action` block is a 409, not a 500. See
  [Errors](https://www.fastapi-restly.org/errors.html).
- React Admin: `AsyncReactAdminView` works with `ra-data-simple-rest`. See
  [React Admin](https://www.fastapi-restly.org/react_admin.html).
- Sync and async: `RestView` and `AsyncRestView` have the same methods.

## What Restly does not do

- Restly does not log users in. FastAPI's security tools find the caller,
  and Restly decides what that caller can see and do.
- Restly does not migrate your database. Use Alembic: `restly new` sets it up.
- Restly is built on SQLAlchemy 2.0. It does not work with other ORMs or with
  document databases.
- The default routes are CRUD on one row at a time. A bulk write, or a write
  of related rows from one body, is code that you add to the view.

See [What the default routes do not do](https://www.fastapi-restly.org/crud_routes.html#crud-limits)
for the details.

## Documentation

- [Tutorial](https://www.fastapi-restly.org/tutorial.html): build a blog API
  step by step, from the first model to authors and drafts.
- [Guide](https://www.fastapi-restly.org/guide.html): one page for each part
  of Restly, how it works and how to use it. Start with
  [Views](https://www.fastapi-restly.org/views.html),
  [CRUD Routes](https://www.fastapi-restly.org/crud_routes.html) and
  [Request Lifecycle & Hooks](https://www.fastapi-restly.org/lifecycle.html).
- [API Reference](https://www.fastapi-restly.org/api_reference.html): exact
  signatures and defaults.

## Examples

Complete applications in
[`example-projects/`](https://github.com/rjprins/fastapi-restly/tree/main/example-projects):

- [Shop](https://github.com/rjprins/fastapi-restly/tree/main/example-projects/shop):
  products, orders and customers.
- [Blog](https://github.com/rjprins/fastapi-restly/tree/main/example-projects/blog):
  the blog that the tutorial builds, with authors, drafts and a publish action.
- [SaaS](https://github.com/rjprins/fastapi-restly/tree/main/example-projects/saas):
  project management with many tenants.

## Contributing

Pull requests and issues are welcome. See
[CONTRIBUTING.md](https://github.com/rjprins/fastapi-restly/blob/main/CONTRIBUTING.md)
for setup and tests. For security issues, see
[SECURITY.md](https://github.com/rjprins/fastapi-restly/blob/main/SECURITY.md).

## License

MIT. See [LICENSE](https://github.com/rjprins/fastapi-restly/blob/main/LICENSE).
