# ![](_static/fr-monogram.svg) FastAPI-Restly

FastAPI-Restly (`fr`) is a REST framework for FastAPI, backed by SQLAlchemy 2.0
and Pydantic v2. Views are real Python classes: share behavior with inheritance
and mixins, and override the one operation you need.

> **Status:** {{ release }}, a public beta after
> [four years of internal use](about.md). Expect small breaking changes in
> deeper extension points on the way to `1.0.0`; see the
> [changelog](changelog.md).

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
GET    /users/       # list users: filter, sort, paginate via URL params
POST   /users/       # create a user
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

- **[Generated REST endpoints](api_reference.md#generated-rest-endpoints)**: GET, POST, PATCH, DELETE with minimal boilerplate
- **[True class-based views](class_based_views.md)**: inheritance, mixins, and method overrides
- **[Explicit override points](customize.md)**: every CRUD verb split into endpoint method, handler, and business method
- **[React Admin ready](howto_react_admin.md)**: `AsyncReactAdminView` speaks `ra-data-simple-rest`
- **[SQLAlchemy 2.0 support](getting_started.md)**: async-first with modern patterns
- **[Pydantic v2 integration](howto_custom_schema.md)**: validation and serialization for public contracts
- **[Automatic schema generation](technical_details.md#auto-generated-schemas)**: read, create, and update schemas generated automatically
- **[List parameters](howto_query_modifiers.md)**: filter, sort, and paginate from a stable URL dialect generated from the response schema
- **[Relationship support](howto_relationship_idschema.md)**: handle foreign keys and nested objects
- **[Testing utilities](howto_testing.md)**: built-in test helpers with savepoint isolation

## Documentation

::::{grid} 1 2 2 3
:gutter: 3

:::{grid-item-card} Getting Started
:link: getting_started
:link-type: doc

Fast path from zero to a working REST API.
:::

:::{grid-item-card} Tutorial
:link: tutorial_overview
:link-type: doc

Build a complete blog API in two parts: generated CRUD, then customization.
:::

:::{grid-item-card} Class-Based Views
:link: class_based_views
:link-type: doc

How subclassable views make the override model work.
:::

:::{grid-item-card} Customize RestView
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

Generated endpoints, all public symbols, query parameters, and autodoc.
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
Tutorial <tutorial_overview>
Views <class_based_views>
Customize <customize>
How-To <user_guide>
examples
API <api_reference>
About <about>
```
