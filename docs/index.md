# FastAPI-Restly Documentation

FastAPI-Restly (`fr`) is a framework that supplements FastAPI with instant CRUD endpoints, built on SQLAlchemy 2.0 and Pydantic v2.

## Quick Start

### Zero-boilerplate mode (auto-schema)

```python
import fastapi_restly as fr
from fastapi import FastAPI
from sqlalchemy.orm import Mapped

app = FastAPI()

# Setup database
fr.setup_async_database_connection("sqlite+aiosqlite:///app.db")

# Define your models
class User(fr.IDBase):
    name: Mapped[str]
    email: Mapped[str]

# Create instant CRUD endpoints
@fr.include_view(app)
class UserView(fr.AsyncAlchemyView):
    prefix = "/users"
    model = User
```

### Explicit schema mode

```python
class UserSchema(fr.IDSchema[User]):
    name: str
    email: str

@fr.include_view(app)
class UserView(fr.AsyncAlchemyView):
    prefix = "/users"
    model = User
    schema = UserSchema
```

Use auto-schema when you want speed and low boilerplate. Use explicit schemas when
you need strict public API contracts, custom validation, aliases, or field-level
serialization control.

## Features

- **Instant CRUD endpoints** - GET, POST, PATCH, DELETE with zero boilerplate
- **SQLAlchemy 2.0 support** - Async-first with modern patterns
- **Pydantic v2 integration** - Full validation and serialization
- **Automatic schema generation** - Create and update schemas generated automatically
- **Query modifiers** - Easy filtering, sorting, and pagination
- **Relationship support** - Handle foreign keys and nested objects
- **Testing utilities** - Built-in test helpers

## Documentation

- [Getting Started](getting_started.md) - Fast path from zero to CRUD API
- [Tutorial](tutorial.md) - Extended walkthrough and schema usage
- [How-To Guides](howto.md) - Task-focused recipes for core features
- [Technical Details](technical_details.md) - How schema generation works under the hood
- [API Reference](api_reference.md) - Complete API documentation

## Installation

```bash
pip install fastapi-restly
```

## Development

```bash
git clone https://github.com/your-repo/fastapi-restly
cd fastapi-restly
uv sync
uv run pytest
```

## Contents
```{toctree}
:maxdepth: 2
:glob:

tutorial
*
```
