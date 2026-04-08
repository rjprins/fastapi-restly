# FastAPI-Restly

[![CI](https://github.com/rjprins/fastapi-restly/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/rjprins/fastapi-restly/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue)](https://github.com/rjprins/fastapi-restly/blob/main/pyproject.toml)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](https://github.com/rjprins/fastapi-restly/blob/main/LICENSE)
[![Coverage](https://rjprins.github.io/fastapi-restly/coverage/badge.svg)](https://rjprins.github.io/fastapi-restly/coverage/)

:::{image} _static/restly-cat.png
:width: 320px
:align: center
:::

FastAPI-Restly (`fr`) is a framework that supplements FastAPI with instant CRUD endpoints, built on SQLAlchemy 2.0 and Pydantic v2.

FastAPI-Restly implements **true class-based views** — real Python classes that support inheritance and method overrides. Share common behavior across views by subclassing, and override individual CRUD methods without touching the rest.

## Quick Start

### Zero-boilerplate mode (auto-schema)

```python
import fastapi_restly as fr
from fastapi import FastAPI
from sqlalchemy.orm import Mapped

app = FastAPI()

# Setup database
fr.configure(async_database_url="sqlite+aiosqlite:///app.db")

# Define your models
class User(fr.IDBase):
    name: Mapped[str]
    email: Mapped[str]

# Create instant CRUD endpoints
@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
```

### Explicit schema mode

```python
class UserSchema(fr.IDSchema):
    name: str
    email: str

@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserSchema
```

Use auto-schema when you want speed and low boilerplate. Use explicit schemas when
you need strict public API contracts, custom validation, aliases, or field-level
serialization control.

## Features

- **Instant CRUD endpoints** — GET, POST, PATCH, DELETE with zero boilerplate
- **True class-based views** — Real inheritance and method overrides; share logic across views by subclassing
- **React Admin ready** — `AsyncReactAdminView` speaks `ra-data-simple-rest` out of the box; no custom data provider needed
- **SQLAlchemy 2.0 support** — Async-first with modern patterns
- **Pydantic v2 integration** — Full validation and serialization
- **Automatic schema generation** — Create and update schemas generated automatically
- **Query modifiers** — Easy filtering, sorting, and pagination
- **Relationship support** — Handle foreign keys and nested objects
- **Testing utilities** — Built-in test helpers with savepoint isolation

## Documentation

::::{grid} 1 2 2 3
:gutter: 3

:::{grid-item-card} Getting Started
:link: getting_started
:link-type: doc

Fast path from zero to a working CRUD API.
:::

:::{grid-item-card} User Guide
:link: user_guide
:link-type: doc

Tutorial walkthroughs and in-depth topic guides covering every framework feature.
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
::::

## Installation

```bash
git clone https://github.com/rjprins/fastapi-restly.git
cd fastapi-restly
uv sync
```

## Development

```bash
git clone https://github.com/rjprins/fastapi-restly.git
cd fastapi-restly
uv sync
uv run pytest
```

```{toctree}
:maxdepth: 2
:hidden:

getting_started
user_guide
api_reference
about
```
