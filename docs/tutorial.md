# Tutorial Part 1: Generated CRUD

Part 1 builds a small blog API with two related models and shows the most common
FastAPI-Restly patterns: models, schemas, and a view class that generates full CRUD
endpoints. It assumes you have read [Getting Started](getting_started.md) and
installed `fastapi-restly[standard]` with the `aiosqlite` driver.

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

{class}`IDBase <fastapi_restly.models.IDBase>` automatically derives table names from the class name using snake_case conversion:
`Post` becomes `"post"`, `Comment` becomes `"comment"`, `BlogPost` would become `"blog_post"`.
This is why `ForeignKey("post.id")` is the correct reference for `Post.id`.

### IDBase and dataclass semantics

{class}`IDBase <fastapi_restly.models.IDBase>` uses SQLAlchemy's `MappedAsDataclass`. Always pass fields as keyword arguments:

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

{class}`fr.IDSchema <fastapi_restly.schemas.IDSchema>` is a Pydantic base class that adds a read-only `id` field to your schema.
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

Declaring the field as `fr.IDRef[Post]` is what triggers this behaviour: the view
machinery stores the id in the matching `post_id` column, and it also validates
that a `Post` with that `id` exists (returning 404 if not). The column can be
named anything — Restly matches the field to the model's mapper, not to an `_id`
suffix.

If you prefer a plain `int` field and want to skip the existence check,
declare `post_id: int` in your schema instead.

See [Work with Foreign Keys Using IDRef](howto_relationship_idschema.md)
for more detail, including list relations and nested relationship objects.

---

## App setup

```python
@asynccontextmanager
async def lifespan(_app: FastAPI):
    await fr.db.async_create_all(fr.IDBase)  # IDBase is the models' base above
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
For production projects, use Alembic migrations instead of {func}`create_all <fastapi_restly.db.create_all>`.

---

## Run it

```bash
fastapi dev main.py
```

Open <http://127.0.0.1:8000/docs> — both resources are listed with their
request and response schemas, ready to try from the browser.

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

The {attr}`prefix <fastapi_restly.views.View.prefix>` value must include the leading slash (e.g. `"/posts"`, not `"posts"`).

To disable specific endpoints, set {attr}`exclude_routes <fastapi_restly.views.BaseRestView.exclude_routes>`:

```python
class PostView(fr.AsyncRestView):
    prefix = "/posts"
    model = Post
    schema = PostRead
    exclude_routes = (fr.ViewRoute.DELETE,)  # disables DELETE /posts/{id}
```

---

## Read-only and write-only fields

Let's give `Post` an author token that clients send on creation but never see
back, and a view count that is server-maintained and must not be writable.
Add the columns to the model:

```python
class Post(fr.IDBase):
    title: Mapped[str]
    content: Mapped[str]
    published: Mapped[bool] = mapped_column(default=False)
    author_token: Mapped[str] = mapped_column(default="")
    view_count: Mapped[int] = mapped_column(default=0)
```

and mark them in the schema:

```python
class PostRead(fr.IDSchema):
    title: str
    content: str
    published: bool
    author_token: fr.WriteOnly[str] = ""  # accepted on input, stripped from responses
    view_count: fr.ReadOnly[int] = 0      # returned in responses, ignored on input
```

- `ReadOnly` fields appear in responses but are ignored on create and update —
  the server owns them.
- `WriteOnly` fields are accepted on create and update but stripped from every
  generated response.

`id` on {class}`IDSchema <fastapi_restly.schemas.IDSchema>` is already `ReadOnly`, which is why it appears in responses without
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

See [Filter, Sort, and Paginate Lists](howto_query_modifiers.md)
for the full list of operators.

---

## Testing

FastAPI-Restly provides {class}`RestlyTestClient <fastapi_restly.testing.RestlyTestClient>`, a thin wrapper around FastAPI's `TestClient`
that asserts sensible default status codes and gives clear failure messages.

```python
from fastapi_restly.testing import RestlyTestClient

client = RestlyTestClient(app)

post = client.post("/posts/", json={"title": "Hello", "content": "World", "published": False})
# Automatically asserts status 201

item = client.get(f"/posts/{post.json()['id']}")
# Automatically asserts status 200
```

For test isolation, install the testing extra (`pip install "fastapi-restly[testing]"`);
pytest then auto-loads Restly's fixtures. The `restly_client` fixture used below wraps
each test in a database savepoint, so changes never persist between tests:

```python
# test_posts.py
def test_create_post(restly_client):
    resp = restly_client.post("/posts/", json={"title": "Hi", "content": "...", "published": False})
    assert resp.json()["title"] == "Hi"
    # Database changes are rolled back automatically after this test
```

See [Testing](howto_testing.md) for the full setup and savepoint details.

---

## Nested Schemas

Response schemas may nest related objects (Restly eager-loads and serializes
them); create/update payloads may not — inputs map to model attributes or use
`*_id: IDRef[Model]`. Details:
[Work with Foreign Keys Using IDRef](howto_relationship_idschema.md).

---

## The complete file

Everything this page built, as one runnable `main.py` (including the
read-only/write-only columns added along the way):

```python
from contextlib import asynccontextmanager

import fastapi_restly as fr
from fastapi import FastAPI
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

fr.configure(async_database_url="sqlite+aiosqlite:///blog.db")


class Post(fr.IDBase):
    title: Mapped[str]
    content: Mapped[str]
    published: Mapped[bool] = mapped_column(default=False)
    author_token: Mapped[str] = mapped_column(default="")
    view_count: Mapped[int] = mapped_column(default=0)


class Comment(fr.IDBase):
    content: Mapped[str]
    post_id: Mapped[int] = mapped_column(ForeignKey("post.id"))


class PostRead(fr.IDSchema):
    title: str
    content: str
    published: bool
    author_token: fr.WriteOnly[str] = ""
    view_count: fr.ReadOnly[int] = 0


class CommentRead(fr.IDSchema):
    content: str
    post_id: fr.IDRef[Post]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await fr.db.async_create_all(fr.IDBase)
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

---

## Next steps

- **[Part 2: Customizing Views](tutorial_customizing.md)** — override handlers, add custom routes, and share behaviour with base classes
- [Auto-Generated Schemas](technical_details.md#auto-generated-schemas) — skip writing schemas for simple models
- [Filter, Sort, and Paginate Lists](howto_query_modifiers.md) — full filter and sort reference
- [Work with Foreign Keys Using IDRef](howto_relationship_idschema.md) — reference related rows by id
- [Testing](howto_testing.md) — savepoint isolation and test fixtures
- [Examples](examples.md) — complete sample apps that extend these patterns
- [API Reference](api_reference.md)
