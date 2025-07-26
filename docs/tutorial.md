# Tutorial

This tutorial will guide you through building a simple CRUD API with FastAPI-Alchemy.

## Installation

```bash
pip install fastapi-alchemy
```

## Quick Start

Let's create a simple blog API with posts and comments.

```python
import fastapi_alchemy as fa
from fastapi import FastAPI
from sqlalchemy.orm import Mapped

# Setup database
fa.setup_async_database_connection("sqlite+aiosqlite:///blog.db")

app = FastAPI()

# Define your SQLAlchemy models
class Post(fa.IDBase):
    title: Mapped[str]
    content: Mapped[str]
    published: Mapped[bool] = Mapped(default=False)

class Comment(fa.IDBase):
    content: Mapped[str]
    post_id: Mapped[int] = Mapped(foreign_key="post.id")

# Define your Pydantic schemas
class PostSchema(fa.IDSchema[Post]):
    title: str
    content: str
    published: bool

class CommentSchema(fa.IDSchema[Comment]):
    content: str
    post_id: int

# Create views with instant CRUD
@fa.include_view(app)
class PostView(fa.AsyncAlchemyView):
    prefix = "/posts"
    model = Post
    schema = PostSchema

@fa.include_view(app)
class CommentView(fa.AsyncAlchemyView):
    prefix = "/comments"
    model = Comment
    schema = CommentSchema
```

That's it! You now have a fully functional API with:

- `GET /posts/` - List all posts
- `POST /posts/` - Create a new post
- `GET /posts/{id}` - Get a specific post
- `PUT /posts/{id}` - Update a post
- `DELETE /posts/{id}` - Delete a post

And the same for comments.

## Read-Only Fields

FastAPI-Alchemy supports two ways to mark fields as read-only:

### Class-Level Read-Only Fields

You can mark fields as read-only at the class level using the `read_only_fields` class variable:

```python
class UserSchema(fa.IDSchema[User]):
    read_only_fields: ClassVar = ["id", "created_at"]
    name: str
    email: str
    id: int
    created_at: datetime
```

### Field-Level Read-Only Fields

You can mark individual fields as read-only using the `ReadOnly` annotation:

```python
class ProductSchema(fa.IDSchema[Product]):
    name: str
    price: float
    internal_id: fa.ReadOnly[str]  # This field is read-only
    created_by: fa.ReadOnly[int]   # This field is also read-only
```

Read-only fields are:
- **Ignored during creation** - They won't appear in the creation schema
- **Ignored during updates** - They won't appear in the update schema  
- **Preserved in responses** - They'll still be included in GET responses
- **Marked in OpenAPI docs** - They'll be marked as read-only in the API documentation

## Database Setup

The framework automatically creates tables for your models. For production, you should use migrations:

```python
# Create tables (development only)
import asyncio
from fastapi_alchemy._globals import fa_globals

async def create_tables():
    engine = fa_globals.async_make_session.kw["bind"]
    async with engine.begin() as conn:
        await conn.run_sync(fa.SQLBase.metadata.create_all)

asyncio.run(create_tables())
```

## Testing

Test your API with the FastAPI test client:

```python
from fastapi.testclient import TestClient

client = TestClient(app)

# Create a post
response = client.post("/posts/", json={
    "title": "My First Post",
    "content": "Hello, world!",
    "published": False
})
print(response.json())

# Get all posts
response = client.get("/posts/")
print(response.json())
```

## Next Steps

- [Query Modifiers](query_modifiers.md) - Filter, sort, and paginate your data
- [Relationships](relationships.md) - Handle foreign keys and nested objects
- [Testing](testing.md) - Write tests for your API
- [Technical Details](technical_details.md) - Learn how the framework works under the hood



