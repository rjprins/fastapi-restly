# FastAPI-Ding

A framework that makes building CRUD APIs with FastAPI and SQLAlchemy incredibly simple.

## Quick Start

```python
import fastapi_ding as fd
from fastapi import FastAPI
from sqlalchemy.orm import Mapped

# Setup database
fd.setup_async_database_connection("sqlite+aiosqlite:///app.db")

app = FastAPI()

# Define your models
class User(fd.IDBase):
    name: Mapped[str]
    email: Mapped[str]

class Post(fd.IDBase):
    title: Mapped[str]
    content: Mapped[str]
    author_id: Mapped[int] = Mapped(foreign_key="user.id")

# Define your schemas
class UserSchema(fd.IDSchema[User]):
    name: str
    email: str

class PostSchema(fd.IDSchema[Post]):
    title: str
    content: str
    author_id: int
    # Field-level read-only using square brackets
    internal_id: fd.ReadOnly[str]

# Create instant CRUD endpoints
@fd.include_view(app)
class UserView(fd.AsyncAlchemyView):
    prefix = "/users"
    model = User
    schema = UserSchema

@fd.include_view(app)
class PostView(fd.AsyncAlchemyView):
    prefix = "/posts"
    model = Post
    schema = PostSchema
```

That's it! You now have a fully functional API with automatic CRUD operations, validation, and OpenAPI documentation.

## Features

- **Instant CRUD**: Automatic endpoints for Create, Read, Update, Delete operations
- **Type Safety**: Full type hints and validation with Pydantic
- **Read-Only Fields**: Mark fields as read-only using `fd.ReadOnly[type]` or class-level `read_only_fields`
- **Relationships**: Handle foreign keys and nested objects seamlessly
- **Query Modifiers**: Built-in filtering, sorting, and pagination
- **Async Support**: Full async/await support with SQLAlchemy 2.0
- **OpenAPI**: Automatic API documentation
- **Testing**: Easy testing with FastAPI's TestClient

## Installation

```bash
pip install fastapi-ding
```

## Documentation

- [Tutorial](docs/tutorial.md) - Get started with FastAPI-Ding
- [Technical Details](docs/technical_details.md) - Learn how the framework works
- [Examples](example-projects/) - See real-world examples

## Examples

Check out the [example projects](example-projects/) for complete applications:

- **Shop**: E-commerce API with products, orders, and customers
- **Blog**: Simple blog with posts and comments
