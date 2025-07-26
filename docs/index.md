# FastAPI-Ding Documentation

FastAPI-Ding (`fd`) is a framework that supplements FastAPI with instant CRUD endpoints, built on SQLAlchemy 2.0 and Pydantic v2.

## Quick Start

```python
import fastapi_ding as fd
from fastapi import FastAPI
from sqlalchemy.orm import Mapped

app = FastAPI()

# Setup database
fd.setup_async_database_connection("sqlite+aiosqlite:///app.db")

# Define your models
class User(fd.IDBase):
    name: Mapped[str]
    email: Mapped[str]

# Define your schemas
class UserSchema(fd.IDSchema[User]):
    name: str
    email: str

# Create instant CRUD endpoints
@fd.include_view(app)
class UserView(fd.AsyncAlchemyView):
    prefix = "/users"
    model = User
    schema = UserSchema
```

## Features

- **Instant CRUD endpoints** - GET, POST, PUT, DELETE with zero boilerplate
- **SQLAlchemy 2.0 support** - Async-first with modern patterns
- **Pydantic v2 integration** - Full validation and serialization
- **Automatic schema generation** - Create and update schemas generated automatically
- **Query modifiers** - Easy filtering, sorting, and pagination
- **Relationship support** - Handle foreign keys and nested objects
- **Testing utilities** - Built-in test helpers

## Documentation

- [Tutorial](tutorial.md) - Complete getting started guide
- [Technical Details](technical_details.md) - How schema generation works under the hood
- [API Reference](api_reference.md) - Complete API documentation

## Installation

```bash
pip install fastapi-ding
```

## Development

```bash
git clone https://github.com/your-repo/fastapi-ding
cd fastapi-ding
uv sync
uv run pytest
```
