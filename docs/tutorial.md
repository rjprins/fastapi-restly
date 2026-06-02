# Tutorial

Run `uv sync` first; see [Getting Started](getting_started.md) for installation.

This tutorial builds a small blog API with two related models and shows the most common
FastAPI-Restly patterns. It assumes you have already read [Getting Started](getting_started.md).

This tutorial uses explicit schemas for clarity. For faster scaffolding, you can omit
`schema = ...` on a view and let FastAPI-Restly auto-generate it from the model.
See [Auto-Generated Schemas](technical_details.md#auto-generated-schemas).

---

## Models

```python
import fastapi_restly as fr
from contextlib import asynccontextmanager
from fastapi import FastAPI
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

fr.configure(async_database_url="sqlite+aiosqlite:///blog.db")


class Post(fr.IDBase):
    title: Mapped[str]
    content: Mapped[str]
    published: Mapped[bool] = mapped_column(default=False)


class Comment(fr.IDBase):
    content: Mapped[str]
    post_id: Mapped[int] = mapped_column(ForeignKey("post.id"))
```

### Table naming

`IDBase` automatically derives table names from the class name using snake_case conversion:
`Post` becomes `"post"`, `Comment` becomes `"comment"`, `BlogPost` would become `"blog_post"`.
This is why `ForeignKey("post.id")` is the correct reference for `Post.id`.

### IDBase and dataclass semantics

`IDBase` uses SQLAlchemy's `MappedAsDataclass`. Always pass fields as keyword arguments:

```python
Post(title="Hello", content="World", published=False)  # correct
```

The `id` column is excluded from `__init__` automatically — you do not pass it.

---

## Schemas

```python
class PostRead(fr.IDSchema):
    title: str
    content: str
    published: bool


class CommentRead(fr.IDSchema):
    content: str
    post_id: fr.IDRef[Post]
```

### What IDSchema provides

`fr.IDSchema` is a Pydantic base class that adds a read-only `id` field to your schema.
Because `id` is `ReadOnly`, it appears in responses but is ignored when creating or updating
records. You do not need to declare `id` yourself.

### Foreign keys with IDRef

`post_id: fr.IDRef[Post]` declares a foreign-key reference. The wire format is
the raw id:

```json
1
```

So a `POST /comments/` request body looks like:

```json
{
  "content": "Great post!",
  "post_id": 1
}
```

And a response looks like:

```json
{
  "id": 7,
  "content": "Great post!",
  "post_id": 1
}
```

The `_id` suffix on the field name is what triggers this behaviour: the view machinery
stores the id in the `post_id` column, and it also validates that a `Post` with
that `id` exists (returning 404 if not).

If you prefer a plain `int` field and want to skip the existence check,
declare `post_id: int` in your schema instead.

See [How-To: Work with Foreign Keys Using IDRef](howto_relationship_idschema.md)
for more detail, including list relations and nested relationship objects.

---

## App setup

```python
@asynccontextmanager
async def lifespan(_app: FastAPI):
    engine = fr.db.get_async_engine()
    async with engine.begin() as conn:
        await conn.run_sync(fr.DataclassBase.metadata.create_all)
    yield


app = FastAPI(lifespan=lifespan)


@fr.include_view(app)
class PostView(fr.AsyncRestView):
    prefix = "/posts"
    model = Post
    schema = PostRead


@fr.include_view(app)
class CommentView(fr.AsyncRestView):
    prefix = "/comments"
    model = Comment
    schema = CommentRead
```

Tables are created inside a FastAPI `lifespan` context manager so they are initialised
after the event loop starts. This is safe with both `uvicorn` and testing tools.
For production projects, use Alembic migrations instead of `create_all`.

---

## Generated endpoints

For each view, FastAPI-Restly generates five endpoints. With `prefix = "/posts"`:

| Method   | Path          | Action         |
|----------|---------------|----------------|
| `GET`    | `/posts/`     | List all posts |
| `POST`   | `/posts/`     | Create a post  |
| `GET`    | `/posts/{id}` | Get one post   |
| `PATCH`  | `/posts/{id}` | Update a post  |
| `DELETE` | `/posts/{id}` | Delete a post  |

The `prefix` value must include the leading slash (e.g. `"/posts"`, not `"posts"`).

To disable specific endpoints, set `exclude_routes`:

```python
class PostView(fr.AsyncRestView):
    prefix = "/posts"
    model = Post
    schema = PostRead
    exclude_routes = (fr.ViewRoute.DELETE,)  # disables DELETE /posts/{id}
```

---

## Read-only and write-only fields

Say you want to add an author token that is stored on creation but stripped by
`self.to_response_schema(obj)`, and a `slug` field that is computed server-side
and must not be writable:

```python
class PostRead(fr.IDSchema):
    title: str
    content: str
    published: bool
    author_token: fr.WriteOnly[str]  # accepted on input, stripped by to_response_schema()
    slug: fr.ReadOnly[str]           # returned in responses, ignored on create/update
```

- `ReadOnly` fields appear in responses but are ignored on create and update.
- `WriteOnly` fields are accepted on create and update. They are removed only
  when Restly serializes an object through `self.to_response_schema(obj)`, which
  the generated CRUD and ReactAdmin routes use.

`id` on `IDSchema` is already `ReadOnly`, which is why it appears in responses without
being part of the create/update body.

---

## Querying lists

List endpoints accept filtering, sorting, and pagination through URL
query parameters. Filters use direct field names with optional operator
suffixes:

```text
GET /posts/?published=true&sort=-id&page=1&page_size=10
GET /posts/?title__icontains=hello
GET /posts/?created_at__gte=2024-01-01&created_at__lt=2025-01-01
```

See [How-To: Filter, Sort, and Paginate Lists](howto_query_modifiers.md)
for the full list of operators.

---

## Testing

FastAPI-Restly provides `RestlyTestClient`, a thin wrapper around FastAPI's `TestClient`
that asserts sensible default status codes and gives clear failure messages.

```python
from fastapi_restly.testing import RestlyTestClient

client = RestlyTestClient(app)

post = client.post("/posts/", json={"title": "Hello", "content": "World", "published": False})
# Automatically asserts status 201

item = client.get(f"/posts/{post.json()['id']}")
# Automatically asserts status 200
```

For test isolation, use the `restly_async_session` or `restly_session` pytest fixtures. These wrap each
test in a database savepoint so changes never persist between tests:

Pytest auto-loads Restly's fixtures after installing the testing extra.

```python
# test_posts.py
def test_create_post(restly_client):
    resp = restly_client.post("/posts/", json={"title": "Hi", "content": "...", "published": False})
    assert resp.json()["title"] == "Hi"
    # Database changes are rolled back automatically after this test
```

See [How-To: Testing](howto_testing.md) and [Pytest Fixtures](pytest_fixtures.md) for the
full setup and savepoint details.

---

## Nested Schemas

Nested schemas are supported for **responses** and relation filtering. If a response schema
includes nested related objects, Restly eagerly loads those relationships and serializes the
nested payloads, including aliases.

Nested schemas are **not** supported for create/update payloads. `POST` and `PATCH` inputs must
still map directly to model attributes or use the `*_id: IDRef[Model]` pattern for foreign
keys. If you need a nested request shape, flatten it in the schema or override the `create` /
`update` business verb and transform the payload yourself. See
[Part 2: Customizing Views](tutorial_customizing.md) for how those verbs fit together.

---

## Next steps

- **[Part 2: Customizing Views](tutorial_customizing.md)** — override handlers, add custom routes, and share behaviour with base classes
- [Auto-Generated Schemas](technical_details.md#auto-generated-schemas) — skip writing schemas for simple models
- [How-To: Filter, Sort, and Paginate Lists](howto_query_modifiers.md) — full filter and sort reference
- [How-To: Foreign Keys with IDRef](howto_relationship_idschema.md) — reference related rows by id
- [How-To: Testing](howto_testing.md) — savepoint isolation and test fixtures
- [API Reference](api_reference.md)
