# ![](_static/fr-monogram.svg) FastAPI-Restly

**Class-based REST resources for FastAPI and SQLAlchemy 2.0. Minimal
boilerplate, full control.**

```python
import fastapi_restly as fr

@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User  # your SQLAlchemy model
```

This view serves the five CRUD routes under `/users`, with Pydantic schemas
made from the model, and filtering, sorting and pagination on the list. To
change one step, override its method, and the rest stays the same.

Restly is for REST resources, not for your whole app. It works inside the
FastAPI app that you already have, and you add it one resource at a time;
see [Use Restly in an existing project](#add-to-existing-app).

> **Status:** {{ release }}, a beta. Restly ran in production for
> [four years](about.md) before it became public. Until 1.0, a minor release
> can have breaking changes, and the [changelog](changelog.md) says what to
> change in your code. Subscribe to the [newsletter](#newsletter-signup) to
> get an email when 1.0 is released. If something is broken or missing,
> please [open an issue](https://github.com/rjprins/fastapi-restly/issues).

[How Restly works](#how-restly-works) shows all the parts on one screen, and
the [Guide](guide.md) explains each part and how you can change it.

## Quick Start

Install FastAPI-Restly along with an async SQLite driver:

```bash
pip install "fastapi-restly[standard]" aiosqlite
```

A SQLAlchemy model and a four-line view class make a complete application.
Copy this whole example into `main.py`:

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

Run `fastapi dev main.py`, and open <http://127.0.0.1:8000/docs>. The view
has the routes below. Their Pydantic schemas are
[generated from the model](#auto-generated-schemas), and you can
[customize them](schemas.md).

```text
GET    /users       # list users: filter, sort, paginate via URL params
POST   /users       # create a user
GET    /users/{id}  # read one user
PATCH  /users/{id}  # partially update one user
DELETE /users/{id}  # delete one user
```

The list returns a page of at most 50 rows, in an envelope:
`{"data": [...], "total_count": 2, "page": 1, "page_size": 50, "total_pages": 1}`.

[Build a Blog API](tutorial.md) teaches Restly step by step, from the first
model to authors, drafts, and tests.

## What Restly does differently

### Change one step, keep the rest

Each CRUD route runs a fixed list of steps. To change one step, override its
method. Add this {meth}`delete <fastapi_restly.views.RestView.delete>` method
to `UserView`, so that it keeps the row and marks the user as inactive:

```python
    async def delete(self, user):
        user.active = False  # keep the row
```

`DELETE /users/{id}` still runs
{meth}`authorize <fastapi_restly.views.RestView.authorize>`, commits and
returns 204. The other four routes do not change. See
[Request Lifecycle & Hooks](lifecycle.md).

### A hidden row is a 404, a refused action is a 403

A scope on the model decides which rows exist for a request. Restly applies
it when a view lists, reads, updates or deletes rows of the model, and in
every reference check. The blog example shows published posts, and the
drafts of the caller:

```{literalinclude} ../example-projects/blog/blog/main.py
:language: python
:pyobject: PostClauses
```

In the scope, `Current.user_id` is a placeholder for the id of the caller.
Restly fills it in each time it applies the scope; see
[Request Context](context.md).
Another user's draft is a 404 on `GET`, `PATCH` and `DELETE`, and the list
does not show or count it.
{meth}`authorize <fastapi_restly.views.RestView.authorize>` decides what the
caller may do with a row that they can see, and a refusal is a 403. See
[Scopes](scopes.md) and [Authorization](authorization.md).

### One schema for requests and responses

Write one Pydantic schema for the resource.
{data}`fr.ReadOnly <fastapi_restly.schemas.ReadOnly>` marks a field that only
goes out, and {data}`fr.WriteOnly <fastapi_restly.schemas.WriteOnly>` marks a
field that only comes in. Restly builds the response class and the two
request bodies from it, with names that OpenAPI and generated clients show.
Here the `User` model also has `password`, `team_id` and `created_at`
columns:

```python
class UserSchema(fr.IDSchema):
    name: str
    email: str
    password: fr.WriteOnly[str]
    team_id: fr.MustExist[int, Team]
    created_at: fr.ReadOnly[fr.UTCDatetime]
```

| Class | Used for | Fields |
|---|---|---|
| `UserResponse` | every response | `id`, `name`, `email`, `team_id`, `created_at` |
| `UserCreate` | the body of `POST` | `name`, `email`, `password`, `team_id` |
| `UserUpdate` | the body of `PATCH` | the same as `UserCreate`, and the client can leave out any of them |

`team_id` is a reference: Restly checks that the team exists, inside the
scope of `Team`. A wrong id is a 404. To make a body or the response
different, write that schema yourself and set it on the view. See
[Schemas & Validation](schemas.md).

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

See [New & Existing Projects](projects.md).

## Also in Restly

- Views without CRUD: {class}`fr.View <fastapi_restly.views.View>` groups
  custom routes, such as login or webhooks, and declares their dependencies
  once. See [Views](views.md).
- List params: `GET /users?name=John&sort=-id&page=2`, and operators such as
  `email__icontains=example.com`. The keys come from the response class, and
  an unknown key is a 422. See [Filtering, Sorting & Pagination](filtering.md).
- Tests: `pip install "fastapi-restly[testing]"` adds pytest fixtures. Each
  test runs in a transaction that rolls back, so a test needs no cleanup code.
  See [Testing](testing.md).
- A new project: `restly new myapp` writes a complete project, with a `users`
  resource and its tests. See [New & Existing Projects](projects.md).
- Database conflicts: a duplicate unique value in a CRUD write or a
  `write_action` block is a 409, not a 500. See [Errors](errors.md).
- React Admin:
  {class}`AsyncReactAdminView <fastapi_restly.views.AsyncReactAdminView>`
  works with `ra-data-simple-rest`. See [React Admin](react_admin.md).
- Sync and async: {class}`RestView <fastapi_restly.views.RestView>` and
  {class}`AsyncRestView <fastapi_restly.views.AsyncRestView>` have the same
  methods.

## What Restly does not do

- Restly does not log users in. FastAPI's security tools find the caller,
  and Restly decides what that caller can see and do.
- Restly does not migrate your database. Use Alembic: `restly new` sets it up.
- Restly is built on SQLAlchemy 2.0. It does not work with other ORMs or with
  document databases.
- The default routes are CRUD on one row at a time. A bulk write, or a write
  of related rows from one body, is code that you add to the view.

See [What the default routes do not do](#crud-limits) for the details.

## Documentation

::::{grid} 1 2 2 2
:gutter: 3

:::{grid-item-card} Tutorial
:link: tutorial
:link-type: doc

Build a blog API step by step: posts and comments, then authors and drafts.
:::

:::{grid-item-card} Guide
:link: guide
:link-type: doc

One page for each part of Restly: how it works, and how to use it.
:::

:::{grid-item-card} API
:link: api_reference
:link-type: doc

Public Python API by module, with signatures and default behavior.
:::

:::{grid-item-card} About
:link: about
:link-type: doc

History, design goals, and why this framework exists.
:::
::::

(newsletter-signup)=
## Newsletter

An email when there is a new release: what changed, and what is worth
upgrading for. Subscribe to get an email when 1.0 is released. You can
unsubscribe from any email.

```{raw} html
:file: _templates/newsletter-form.html
```

```{toctree}
:maxdepth: 2
:hidden:

Tutorial <tutorial>
Guide <guide>
API <api_reference>
Blog <https://www.fastapi-restly.org/blog/>
About <about>
```
