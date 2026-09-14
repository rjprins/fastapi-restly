# ![](_static/fr-monogram.svg) FastAPI-Restly

FastAPI-Restly (`fr`) is a REST framework for FastAPI, using SQLAlchemy 2.0 and
Pydantic v2. Views are real Python classes that support inheritance and mixins.
Designed for customization.

> **Status:** {{ release }}, a public beta after
> [four years of internal use](about.md). Expect small breaking changes in
> deeper extension points on the way to `1.0.0`; see the
> [changelog](changelog.md).

With FastAPI-Restly imported as `fr`, a CRUD resource is four lines once `app`
and `User` exist:

```python
@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User  # SQLAlchemy model
```

See [Using RestView](rest_views.md) for the full guide and [Customizing
RestView](customize.md) for how you can change it.

## Quick Start

Install FastAPI-Restly along with an async SQLite driver:

```bash
pip install "fastapi-restly[standard]" aiosqlite
```

A SQLAlchemy model and a four-line view class make a complete, runnable
application:

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

That view exposes these routes, with schemas generated from the model:

```text
GET    /users       # list users: filter, sort, paginate via URL params
POST   /users       # create a user
GET    /users/{id}   # read one user
PATCH  /users/{id}   # partially update one user
DELETE /users/{id}   # delete one user
```

Because the view is a class, changing one behavior means overriding one
method; routing, validation, and the commit stay framework-owned. Add this
method to `UserView`:

```python
    async def delete(self, obj):
        obj.active = False  # deactivate instead of removing the row
```

`DELETE /users/{id}` now soft-disables (the row stays readable, with
`active: false`); the other four routes are untouched.

Run it with `fastapi dev main.py`. [Getting Started](getting_started.md) walks
through the same flow step by step: install details, explicit schemas, and a
first test.

## Features

- **[Default CRUD routes](api_reference.md#default-crud-routes)**: GET, POST, PATCH, DELETE with minimal boilerplate
- **[True class-based views](class_based_views.md)**: inheritance, mixins, and method overrides
- **[Explicit override points](customize.md)**: every CRUD verb split into an endpoint method and a business method you override, with a final handler between them that custom routes call
- **[React Admin ready](howto_react_admin.md)**: `AsyncReactAdminView` speaks `ra-data-simple-rest`
- **[SQLAlchemy 2.0 support](getting_started.md)**: async-first with modern patterns
- **[Pydantic v2 integration](howto_custom_schema.md)**: validation and serialization for public contracts
- **[Automatic schema generation](technical_details.md#auto-generated-schemas)**: read, create, and update schemas generated automatically
- **[List parameters](howto_query_modifiers.md)**: filter, sort, and paginate from a stable URL dialect generated from the response schema
- **[Scopes](scopes.md)**: row visibility declared once as a clause and applied to every read and reference check
- **[Relationship support](howto_relationship_idschema.md)**: handle foreign keys and nested objects
- **[Testing utilities](howto_testing.md)**: built-in test helpers with savepoint isolation

## Documentation

::::{grid} 1 2 2 3
:gutter: 3

:::{grid-item-card} Getting Started
:link: getting_started
:link-type: doc

Build a working REST API, then continue with a two-model blog API.
:::

:::{grid-item-card} Views
:link: class_based_views
:link-type: doc

Choose a view type, then learn how registration, dependencies, and inheritance
work.
:::

:::{grid-item-card} Using RestView
:link: rest_views
:link-type: doc

Define a model-backed CRUD resource and understand its default contract.
:::

:::{grid-item-card} Customizing RestView
:link: customize
:link-type: doc

The three tiers behind every CRUD verb, with recipes for every override
point.
:::

:::{grid-item-card} How-To Guides
:link: user_guide
:link-type: doc

Task-focused guides, from adoption in an existing app to deployment.
:::

:::{grid-item-card} Examples
:link: examples
:link-type: doc

Complete sample applications from a tiny API to a production-shaped service.
:::

:::{grid-item-card} API Reference
:link: api_reference
:link-type: doc

Default CRUD routes, all public symbols, query parameters, and autodoc.
:::

:::{grid-item-card} About
:link: about
:link-type: doc

History, design goals, and why this framework exists.
:::

:::{grid-item-card} Deploying
:link: deploying
:link-type: doc

Production engine config, Alembic migrations, and an ASGI checklist.
:::
::::

```{toctree}
:maxdepth: 2
:hidden:

getting_started
Tutorial <tutorial>
CRUD Views <class_based_views>
How-To <user_guide>
API <api_reference>
Blog <https://www.fastapi-restly.org/blog/>
examples
About <about>
```
