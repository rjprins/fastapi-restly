# Tutorial

This tutorial builds a small blog API and shows the most common FastAPI-Restly patterns.

If you want the shortest path first, start with [Getting Started](getting_started.md).

This tutorial uses explicit schemas for clarity. For faster scaffolding, you can
omit `schema = ...` on a view and let FastAPI-Restly auto-generate it from the model.

## Blog API Example

```python
import fastapi_restly as fr
from fastapi import FastAPI
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import ForeignKey

fr.setup_async_database_connection("sqlite+aiosqlite:///blog.db")
app = FastAPI()

class Post(fr.IDBase):
    title: Mapped[str]
    content: Mapped[str]
    published: Mapped[bool] = mapped_column(default=False)

class Comment(fr.IDBase):
    content: Mapped[str]
    post_id: Mapped[int] = mapped_column(ForeignKey("post.id"))

class PostSchema(fr.IDSchema[Post]):
    title: str
    content: str
    published: bool

class CommentSchema(fr.IDSchema[Comment]):
    content: str
    post_id: fr.IDSchema[Post]

@fr.include_view(app)
class PostView(fr.AsyncAlchemyView):
    prefix = "/posts"
    model = Post
    schema = PostSchema

@fr.include_view(app)
class CommentView(fr.AsyncAlchemyView):
    prefix = "/comments"
    model = Comment
    schema = CommentSchema
```

## Generated Endpoints

For each view, FastAPI-Restly generates:

- `GET /{prefix}/`
- `POST /{prefix}/`
- `GET /{prefix}/{id}`
- `PATCH /{prefix}/{id}`
- `DELETE /{prefix}/{id}`

## Read-Only and Write-Only Fields

```python
class UserSchema(fr.IDSchema[User]):
    name: str
    email: str
    password: fr.WriteOnly[str]
    internal_id: fr.ReadOnly[str]
```

Behavior:
- `ReadOnly` fields are ignored for writes and included in responses.
- `WriteOnly` fields are accepted in writes and hidden in responses.

## Querying Lists

Use query modifiers on list endpoints:

```text
GET /posts/?filter[published]=true
GET /posts/?sort=-id
GET /posts/?limit=10&offset=0
```

Or V2 style:

```text
GET /posts/?published=true&sort=-id&page=1&page_size=10
```

## Testing

```python
from fastapi.testclient import TestClient

client = TestClient(app)

create = client.post("/posts/", json={"title": "Hello", "content": "World", "published": False})
assert create.status_code == 201

items = client.get("/posts/")
assert items.status_code == 200
```

## Next Steps

- [API Reference](api_reference.md)
- [Query Modifiers](query_modifiers.md)
- [Technical Details](technical_details.md)
- Testing guide: `TESTING.md` (repository root)
