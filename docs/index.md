# FastAPI-Alchemy Documentation

FastAPI-Alchemy (`fa`) is a framework that supplements FastAPI with instant CRUD endpoints, built on SQLAlchemy 2.0 and Pydantic v2.

## Quick Start

```python
import fastapi_alchemy as fa
from fastapi import FastAPI

# Setup database
fa.setup_async_database_connection("sqlite+aiosqlite:///test.db")

# Define models and schemas
class User(fa.IDBase):
    name: Mapped[str]
    email: Mapped[str]

class UserSchema(fa.IDSchema[User]):
    name: str
    email: str

# Create view with instant CRUD
class UserView(fa.AsyncAlchemyView):
    schema = UserSchema

# Include in FastAPI app
app = FastAPI()
fa.include_view(app, UserView)
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
pip install fastapi-alchemy
```

## Development

```bash
git clone https://github.com/your-repo/fastapi-alchemy
cd fastapi-alchemy
uv sync
uv run pytest
```
