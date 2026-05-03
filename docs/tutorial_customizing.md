# Tutorial Part 2: Customizing Views

This tutorial extends the blog API from [Part 1](tutorial.md). It introduces every
layer of the customization system in order, from the simplest override down to
shared base classes.

The examples use `AsyncRestView`. The same handlers and patterns apply to `RestView`
(sync) — just drop the `async`/`await`.

---

## The customization layers

FastAPI-Restly gives you four layers to work with:

```
handle_* handlers            — change what one CRUD operation does
object helpers        — change how objects are built/saved/deleted across all writes
custom routes         — add endpoints beyond the five generated ones
inheritance           — share any of the above across multiple views
```

Start at the highest layer that covers what you need. Drop down only when necessary.

---

## Layer 1 — `handle_*` handlers

Each generated endpoint delegates to a `handle_*` handler. Override the handler to change
the business logic without touching the HTTP contract.

```
GET /          → index()   → handle_list(query_params)
GET /{id}      → get()     → handle_get(id)
POST /         → post()    → handle_create(schema_obj)
PATCH /{id}    → patch()   → handle_update(id, schema_obj)
DELETE /{id}   → delete()  → handle_delete(id)
```

Inside every handler, `self.session` is the live database session and `self.request`
is the FastAPI `Request` object.

### handle_create — inject server-side fields

Real APIs rarely accept every field from the client. Say each post should record
which user created it, taken from the request context rather than from the payload:

```python
@fr.include_view(app)
class PostView(fr.AsyncRestView):
    prefix = "/posts"
    model = Post
    schema = PostSchema

    async def handle_create(self, schema_obj):
        obj = await self.make_new_object(schema_obj)
        obj.author_id = self.request.state.user_id   # set server-side
        return await self.save_object(obj)
```

`make_new_object` builds the ORM instance from the validated payload.
`save_object` flushes and refreshes it. Both are separate steps you can
intercept individually — more on that in Layer 2.

### handle_update — validate before saving

Block updates based on the current state of the object:

```python
    async def handle_update(self, id, schema_obj):
        obj = await self.handle_get(id)          # raises 404 if missing
        if obj.published:
            raise fastapi.HTTPException(409, "Cannot edit a published post")
        obj = await self.update_object(obj, schema_obj)
        return await self.save_object(obj)
```

Calling `self.handle_get(id)` reuses the same 404 logic as the GET endpoint.
If you later override `handle_get` (for example, to add tenant scoping), `handle_update`
picks up that change automatically.

### build_list_query — filter results to the current user

The most common real-world override: restrict the list to rows the caller is
allowed to see. `build_list_query` is the seam both `handle_list` and `count_index`
consult, so a single override keeps the listed rows and the pagination total
in sync.

```python
@fr.include_view(app)
class PostView(fr.AsyncRestView):
    prefix = "/posts"
    model = Post
    schema = PostSchema
    include_pagination_metadata = True

    def build_list_query(self):
        user_id = self.request.state.user_id
        return super().build_list_query().where(Post.author_id == user_id)
```

Calling `super().build_list_query()` and chaining `.where(...)` composes cleanly
with any base-class or mixin filter. Reach for a `handle_list` override only when
you need to do work beyond a `WHERE` clause — see
[Override Endpoints](howto_override_endpoints.md#scope-filter-the-list-endpoint).

### handle_delete — require explicit confirmation

```python
    async def handle_delete(self, id):
        if self.request.headers.get("X-Confirm-Delete") != "yes":
            raise fastapi.HTTPException(400, "Missing X-Confirm-Delete: yes header")
        return await super().handle_delete(id)
```

`super().handle_delete(id)` handles the 404 check and the actual deletion.
Override only the guard; let the base class do the rest.

---

## Layer 2 — object helpers

The object helpers sit below the `handle_*` handlers. They handle the mechanics of
construction, persistence, and removal. Override them when the same change
applies to **both** create and update, so you don't repeat yourself.

```
handle_create  →  make_new_object(schema_obj)
           →  save_object(obj)

handle_update  →  handle_get(id)
           →  update_object(obj, schema_obj)
           →  save_object(obj)

handle_delete  →  handle_get(id)
           →  delete_object(obj)
```

### save_object — run a side effect after every write

If you need to do something after every successful write — send a webhook,
invalidate a cache, emit an event — override `save_object`:

```python
    async def save_object(self, obj):
        obj = await super().save_object(obj)
        await notify_subscribers(obj.id)   # your async side-effect here
        return obj
```

Because `handle_create` and `handle_update` both end with `self.save_object(obj)`,
this one override covers both operations.

### make_new_object — set a default on creation only

If you need to stamp a field only at creation time (not on update):

```python
    async def make_new_object(self, schema_obj):
        obj = await super().make_new_object(schema_obj)
        obj.created_by = self.request.state.user_id
        return obj
```

### update_object — guard fields from being changed

Strip a field from the payload before it reaches the database:

```python
    async def update_object(self, obj, schema_obj):
        schema_obj.author_id = None   # ignore any attempt to change authorship
        return await super().update_object(obj, schema_obj)
```

### delete_object — implement soft-delete

Replace hard-delete with a flag:

```python
from datetime import datetime, timezone

    async def delete_object(self, obj):
        obj.deleted_at = datetime.now(timezone.utc)
        await self.session.flush()
        # Do NOT call super() — that would remove the row.
```

`DELETE /posts/{id}` now marks the row instead of removing it. The 204 response
is still returned by `handle_delete`; only the persistence step changes.

---

## Layer 3 — custom routes

Use `@fr.get`, `@fr.post`, `@fr.patch`, `@fr.put`, or `@fr.delete` to add
endpoints alongside the generated ones.

### A computed read endpoint

Expose a summary of a post without returning the full record:

```python
@fr.include_view(app)
class PostView(fr.AsyncRestView):
    prefix = "/posts"
    model = Post
    schema = PostSchema

    @fr.get("/{id}/summary")
    async def summary(self, id: int):
        post = await self.handle_get(id)   # raises 404 automatically
        return {
            "id": post.id,
            "title": post.title,
            "word_count": len(post.content.split()),
        }
```

Calling `self.handle_get(id)` gives you the ORM object with the same 404 logic
as the standard GET endpoint — and picks up any override you may have applied.

### A state-change action

Add a `publish` action that transitions a post to a published state:

```python
import fastapi

    @fr.post("/{id}/publish", status_code=200)
    async def publish(self, id: int):
        post = await self.handle_get(id)
        if post.published:
            raise fastapi.HTTPException(409, "Already published")
        post.published = True
        post = await self.save_object(post)
        return self.to_response_schema(post)
```

`self.to_response_schema(post)` serializes the ORM object using the view's
configured response schema, exactly as the standard endpoints do.

---

## Layer 4 — inheritance

All of the above can be promoted from a single view to a shared base class.
Because views are plain Python classes, normal inheritance works without any
special framework support.

### Extract authentication into a base class

The blog API has two views that both need a current user. Instead of repeating
the dependency and the `handle_create` logic:

```python
from typing import Annotated
from fastapi import Depends

def get_current_user(request: fastapi.Request) -> User:
    return request.state.user   # your auth logic here


class AuthoredBase(fr.AsyncRestView):
    current_user: Annotated[User, Depends(get_current_user)]

    async def handle_create(self, schema_obj):
        obj = await self.make_new_object(schema_obj)
        obj.author_id = self.current_user.id
        return await self.save_object(obj)


@fr.include_view(app)
class PostView(AuthoredBase):
    prefix = "/posts"
    model = Post
    schema = PostSchema


@fr.include_view(app)
class CommentView(AuthoredBase):
    prefix = "/comments"
    model = Comment
    schema = CommentSchema
```

`self.current_user` is injected by FastAPI's dependency system and is available
in every method of every subclass. `AuthoredBase` itself is never passed to
`include_view` — only the concrete subclasses are registered.

### Layer overrides with super()

A subclass can extend a base-class handler rather than replace it:

```python
@fr.include_view(app)
class PostView(AuthoredBase):
    prefix = "/posts"
    model = Post
    schema = PostSchema

    async def handle_create(self, schema_obj):
        # PostView-specific logic before the base class runs
        schema_obj.slug = slugify(schema_obj.title)
        return await super().handle_create(schema_obj)
```

The call chain is `PostView.handle_create` → `AuthoredBase.handle_create` →
`AsyncRestView.handle_create`. All three layers run in order.

### Apply router-level dependencies

`dependencies = [Depends(fn)]` on a view (or base class) applies `fn` to
every route the view registers — without the dependency result being injected
as an attribute. Use this for authentication guards or rate-limiting:

```python
class ProtectedBase(fr.AsyncRestView):
    dependencies = [Depends(require_auth)]


@fr.include_view(app)
class PostView(ProtectedBase):
    prefix = "/posts"
    model = Post
    schema = PostSchema
```

Every route on `/posts/` now runs `require_auth` before the endpoint function.

### Share a URL namespace with prefix concatenation

When a base class defines `prefix`, subclass prefixes are appended:

```python
class ApiV1(fr.AsyncRestView):
    prefix = "/api/v1"


@fr.include_view(app)
class PostView(ApiV1):
    prefix = "/posts"     # → /api/v1/posts
    model = Post
    schema = PostSchema


@fr.include_view(app)
class CommentView(ApiV1):
    prefix = "/comments"  # → /api/v1/comments
    model = Comment
    schema = CommentSchema
```

---

## Putting it together

Here is the blog API from Part 1, extended with everything from this tutorial:

```python
import fastapi
import fastapi_restly as fr
from datetime import datetime, timezone
from typing import Annotated
from fastapi import Depends
from sqlalchemy import create_engine, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

fr.configure(async_database_url="sqlite+aiosqlite:///blog.db")

app = fastapi.FastAPI()


# --- Models ---

class Post(fr.IDBase):
    title: Mapped[str]
    content: Mapped[str]
    published: Mapped[bool] = mapped_column(default=False)
    author_id: Mapped[int | None] = mapped_column(default=None)
    deleted_at: Mapped[datetime | None] = mapped_column(default=None)


class Comment(fr.IDBase):
    content: Mapped[str]
    post_id: Mapped[int] = mapped_column(ForeignKey("post.id"))
    author_id: Mapped[int | None] = mapped_column(default=None)


# Create tables — must run AFTER model classes are declared so they're registered on the metadata.
fr.DataclassBase.metadata.create_all(create_engine("sqlite:///blog.db"))


# --- Schemas ---

class PostSchema(fr.IDSchema):
    title: str
    content: str
    published: bool


class CommentSchema(fr.IDSchema):
    content: str
    post_id: fr.IDSchema[Post]


# --- Shared base ---

def get_current_user_id(request: fastapi.Request) -> int:
    return request.state.user_id   # set by your auth middleware


class AuthoredBase(fr.AsyncRestView):
    user_id: Annotated[int, Depends(get_current_user_id)]

    async def handle_create(self, schema_obj):
        obj = await self.make_new_object(schema_obj)
        obj.author_id = self.user_id
        return await self.save_object(obj)


# --- Views ---

@fr.include_view(app)
class PostView(AuthoredBase):
    prefix = "/posts"
    model = Post
    schema = PostSchema

    async def handle_update(self, id, schema_obj):
        obj = await self.handle_get(id)
        if obj.published:
            raise fastapi.HTTPException(409, "Cannot edit a published post")
        obj = await self.update_object(obj, schema_obj)
        return await self.save_object(obj)

    async def delete_object(self, obj):
        obj.deleted_at = datetime.now(timezone.utc)
        await self.session.flush()

    @fr.post("/{id}/publish", status_code=200)
    async def publish(self, id: int):
        post = await self.handle_get(id)
        if post.published:
            raise fastapi.HTTPException(409, "Already published")
        post.published = True
        post = await self.save_object(post)
        return self.to_response_schema(post)


@fr.include_view(app)
class CommentView(AuthoredBase):
    prefix = "/comments"
    model = Comment
    schema = CommentSchema
```

---

## Next steps

- [How-To: Override Endpoints](howto_override_endpoints.md) — complete handler reference with all signatures
- [How-To: Share Behaviour with Base Views](howto_inheritance.md) — full inheritance guide
- [How-To: Testing](howto_testing.md) — test the overrides you write
- [API Reference](api_reference.md)
