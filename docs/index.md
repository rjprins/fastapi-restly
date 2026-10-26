# ![](_static/fr-monogram.svg) FastAPI-Restly

FastAPI-Restly (`fr`) is a REST framework for FastAPI, using SQLAlchemy 2.0 and
Pydantic v2, with class-based views that support full customization through
inheritance and mixins.

> **Status:** {{ release }}, a public beta after
> [four years of internal use](about.md). Until `1.0.0`, each minor release
> can have breaking changes; see the [changelog](changelog.md).

With FastAPI-Restly imported as `fr`, a CRUD resource is four lines once `app`
and `User` exist:

```python
@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User  # SQLAlchemy model
```

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

That view exposes the routes below. Their Pydantic schemas are
[generated from the model](#auto-generated-schemas), and you can
[customize them](schemas.md).

```text
GET    /users       # list users: filter, sort, paginate via URL params
POST   /users       # create a user
GET    /users/{id}   # read one user
PATCH  /users/{id}   # partially update one user
DELETE /users/{id}   # delete one user
```

Because the view is a class, changing one behavior means overriding one
method; routing, validation, and the commit can stay framework-owned. Add this
method to `UserView`:

```python
    async def delete(self, user):
        user.active = False  # deactivate instead of removing the row
```

`DELETE /users/{id}` now soft-disables (the row stays readable, with
`active: false`); the other four routes are untouched.

Run it with `fastapi dev main.py`. [Build a Blog API](tutorial.md) teaches
Restly step by step, from the first model to authors, drafts, and tests.

## Features

- **[Default CRUD routes](#default-crud-routes)**: GET, POST, PATCH, DELETE with minimal boilerplate
- **[True class-based views](views.md)**: inheritance, mixins, and method overrides
- **[Explicit override points](lifecycle.md)**: every CRUD verb split into an endpoint method and a business method you override
- **[React Admin ready](react_admin.md)**: `AsyncReactAdminView` speaks `ra-data-simple-rest`
- **[SQLAlchemy 2.0 support](tutorial.md)**: async-first with modern patterns
- **[Pydantic v2 integration](schemas.md)**: validation and serialization for public contracts
- **[Automatic schema generation](#auto-generated-schemas)**: response, create, and update schemas generated automatically
- **[Filtering](filtering.md)**: filter, sort, and paginate with list params generated from the view's schema
- **[Request Context](context.md)**: bind values, such as the current user, to a request-local context and read them anywhere in the request
- **[Scopes](scopes.md)**: control what a view can return with one SQLAlchemy where clause, applied to every read and reference check
- **[Relationship support](relationships.md)**: handle foreign keys and nested objects
- **[Testing utilities](testing.md)**: built-in test helpers with savepoint isolation

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

```{toctree}
:maxdepth: 2
:hidden:

Tutorial <tutorial>
Guide <guide>
API <api_reference>
Blog <https://www.fastapi-restly.org/blog/>
About <about>
```
