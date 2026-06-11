# Tutorial Part 2: Customizing Views

This tutorial extends the blog API from [Part 1](tutorial.md). It introduces customization from single-method overrides to shared base classes.

The examples use `AsyncRestView`. The same methods and patterns apply to `RestView` (sync) — just drop the `async`/`await`.

---

## The three tiers of a CRUD verb

Every CRUD verb has three tiers. Override the lowest tier that owns the
behavior you need. (The full model, lifecycles, and decision table:
[How Overrides Work: The Three Tiers](the_handle_design.md).)

```
<verb>_endpoint   — the route shell (wire boundary): the @route, the FastAPI
                    signature/response_model, and to_response. Rarely overridden.
handle_<verb>     — the request handler: runs authorize and the commit bracket
                    (before_commit → commit → after_commit), returns the domain
                    object. Override to change orchestration/timing.
<verb>            — the business verb: the domain operation (build/apply/save).
                    Auth-free and commit-free. The usual override point.
```

The five verbs are `get_many`, `get_one`, `create`, `update`, and `delete`. So the full call chain for a create is:

```
POST /     → create_endpoint(schema_obj)     # route shell
           → handle_create(schema_obj)       # authorize + commit bracket
           → create(schema_obj)              # build + save, no commit
```

Two facts make this layout safe to override:

- **The handler owns the commit.** `handle_<verb>` runs `before_commit → commit → after_commit` around the business verb.
- **The business verb never commits.** `create` / `update` / `delete` build, apply, and flush. The handler commits later.

Inside every method, `self.session` is the live database session and `self.request` is the FastAPI `Request` object.

---

## Tier 3 — the business verb (the usual override point)

Most customization lives here. The business verb is the domain operation: build an object, apply a payload, save it. It is **auth-free** and **commit-free**; the handler adds authorization and commit handling.

### create — inject server-side fields

Real APIs rarely accept every field from the client. This example stamps the author from request context:

```python
@fr.include_view(app)
class PostView(fr.AsyncRestView):
    prefix = "/posts"
    model = Post
    schema = PostRead

    async def create(self, schema_obj):
        obj = await self.make_new_object(schema_obj)
        obj.author_id = self.request.state.user_id   # set server-side
        return await self.save_object(obj)
```

`make_new_object` builds the ORM instance. `save_object` flushes and refreshes it, but does not commit. For fields stamped on both create and update, override `make_new_object` / `update_object`; see [Stamping extra fields](#stamping-extra-fields).

### update — validate before saving

To reject an update based on current state, override `update`. It receives the loaded object:

```python
    async def update(self, obj, schema_obj):
        if obj.published:
            raise fastapi.HTTPException(409, "Cannot edit a published post")
        obj = await self.update_object(obj, schema_obj)
        return await self.save_object(obj)
```

`handle_update` has already loaded `obj` through `get_one` and run `authorize`. `update` only describes the domain change.

### build_query — filter results to the current user

The common read override is row visibility. `get_many`, `count`, and `get_one` all use `build_query`, so one filter keeps listings, totals, single-row reads, updates, and deletes aligned.

```python
@fr.include_view(app)
class PostView(fr.AsyncRestView):
    prefix = "/posts"
    model = Post
    schema = PostRead
    include_pagination_metadata = True

    def build_query(self):
        user_id = self.request.state.user_id
        return super().build_query().where(Post.author_id == user_id)
```

Calling `super().build_query()` and chaining `.where(...)` composes cleanly with any base-class or mixin filter.

Read access has two halves, and they live in two different tiers:

- **Visibility** — `build_query`. A hidden row is not part of this view, so `get_one` returns 404.
- **Policy** — `authorize`, called in the request handler. Use it for "may this caller read at all", not for "which rows exist".

### delete — implement soft-delete

The `delete` business verb removes the object. Override it to flip a flag instead:

```python
from datetime import datetime, timezone


class PostView(fr.AsyncRestView):
    prefix = "/posts"
    model = Post
    schema = PostRead

    async def delete(self, obj):
        obj.deleted_at = datetime.now(timezone.utc)
        await self.session.flush()
        # Do NOT call super() / delete_object — that would remove the row.
```

`DELETE /posts/{id}` now marks the row instead of removing it. `delete_endpoint` still returns 204, and `handle_delete` still commits. Pair this with a `build_query` filter that hides deleted rows — the canonical recipe lives in [Override CRUD Behavior](#soft-delete-recipe), and the reusable mixin version in [Compose Views with Mixins](howto_compose_views_with_mixins.md).

---

## Tier 2 — the request handler (orchestration and timing)

`handle_<verb>` owns `authorize` and the commit bracket. Override it to change *orchestration or timing* without re-declaring the route. The defaults look like this:

```
handle_create  →  authorize("create", data=schema_obj)
               →  create(schema_obj)
               →  before_commit → commit → after_commit

handle_update  →  get_one(id)                     # loads through build_query
               →  authorize("update", obj, data=schema_obj)
               →  update(obj, schema_obj)
               →  before_commit → commit → after_commit

handle_delete  →  get_one(id)
               →  authorize("delete", obj)
               →  delete(obj)
               →  before_commit → commit → after_commit
```

The transaction hooks are the usual reason to drop to this tier:

- **`before_commit(action, new, old=None)`** — an in-transaction side effect (an outbox row, an audit row) that commits atomically with the write.
- **`after_commit(action, new, old=None)`** — a post-commit side effect (an email, a webhook, a cache invalidation) that runs only after the write is durable.

Both receive `old`, the pre-mutation snapshot produced by `snapshot(obj)`, so you can fire only on a real change:

```python
    async def after_commit(self, action, new, old=None):
        if action == "update" and old["published"] != new.published:
            await notify_subscribers(new.id)
```

The hooks cover most timing needs. Override `handle_<verb>` only when the operation order or transaction must change.

---

## Stamping extra fields

For fields stamped on **both** create and update, override `make_new_object` / `update_object` cooperatively. Call `super()`, mutate, and return. Base classes and mixins then compose cleanly:

```python
    async def make_new_object(self, schema_obj):
        obj = await super().make_new_object(schema_obj)
        obj.created_by = self.request.state.user_id   # stamp the constructed object
        return obj

    async def update_object(self, obj, schema_obj):
        obj = await super().update_object(obj, schema_obj)
        obj.updated_by = self.request.state.user_id
        return obj
```

`make_new_object` builds the ORM object; `update_object` applies the payload. Override them for structural stamps without touching the business verb.

---

## Object utilities

The business verbs are built from a small set of object utilities. These are **utilities you call**, not override points:

```
create  →  make_new_object(schema_obj)   # build ORM object (override point for stamping)
        →  save_object(obj)              # flush + refresh (no commit)

update  →  update_object(obj, schema_obj)  # apply payload (override point for stamping)
        →  save_object(obj)

delete  →  delete_object(obj)              # delete + flush (no commit)
```

`make_new_object` and `update_object` do not flush. `save_object` flushes and refreshes, but does **not** commit. The same operations are available as free functions for services and workers.

---

## Custom routes

Use `@fr.get`, `@fr.post`, `@fr.patch`, `@fr.put`, or `@fr.delete` to add endpoints. Reuse `handle_get_one` for scoped load + read auth, `get_one` for scoped load only, and `save_object` to persist.

All route decorator keyword arguments are passed through to FastAPI. Configure class-based routes the same way you configure regular FastAPI routes: use `response_model=`, `status_code=`, `dependencies=`, `responses=`, and the other FastAPI route options as usual.

### A computed read endpoint

Expose a summary of a post without returning the full record:

```python
@fr.include_view(app)
class PostView(fr.AsyncRestView):
    prefix = "/posts"
    model = Post
    schema = PostRead

    @fr.get("/{id}/summary")
    async def summary(self, id: int):
        post = await self.handle_get_one(id)   # scope + 404 + read-auth
        return {
            "id": post.id,
            "title": post.title,
            "word_count": len(post.content.split()),
        }
```

`handle_get_one(id)` gives the same scope, 404 behavior, and read authorization as `GET /{id}`. Use `get_one(id)` when you want scope and 404 without read authorization.

### A state-change action

Add a `publish` action. Load with `handle_get_one`, then use `write_action` so authorization, snapshot, commit hooks, and commit stay in the framework bracket:

```python
import fastapi

    @fr.post("/{id}/publish", status_code=200)
    async def publish(self, id: int):
        post = await self.handle_get_one(id)
        if post.published:
            raise fastapi.HTTPException(409, "Already published")
        async with self.write_action("publish", obj=post):
            post.published = True
        return self.to_response(post)
```

`self.to_response(post)` serializes through the view's response schema, the
same way the generated routes do.

If a custom action is just a create or update under another URL, call `handle_create` / `handle_update`:

```python
    @fr.post("/{id}/repost")
    async def repost(self, id: int, schema_obj: PostRead):
        original = await self.handle_get_one(id)
        # ... derive a new payload from `original` ...
        return self.to_response(await self.handle_create(schema_obj))
```

`handle_create` runs authorization, your `create` override, and the commit bracket.

---

## Database conflict responses

Restly turns SQLAlchemy `IntegrityError` exceptions into `409 Conflict`
responses by default; custom envelopes and the opt-out are covered in
[Default Exception Handling](api_reference.md#default-exception-handling).

---

## Sharing behaviour with base classes

Any override above can move into a shared base class. Views are plain Python classes, so normal inheritance works.

### Extract authentication into a base class

If several views need the current user, put the dependency and create-time stamp on a shared base:

```python
from typing import Annotated
from fastapi import Depends

def get_current_user(request: fastapi.Request) -> User:
    return request.state.user   # your auth logic here


class AuthoredBase(fr.AsyncRestView):
    current_user: Annotated[User, Depends(get_current_user)]

    async def create(self, schema_obj):
        obj = await self.make_new_object(schema_obj)
        obj.author_id = self.current_user.id
        return await self.save_object(obj)


@fr.include_view(app)
class PostView(AuthoredBase):
    prefix = "/posts"
    model = Post
    schema = PostRead


@fr.include_view(app)
class CommentView(AuthoredBase):
    prefix = "/comments"
    model = Comment
    schema = CommentRead
```

FastAPI injects `self.current_user` on every subclass method. Register only concrete subclasses, not the base.

### Extend a base-class verb with super()

A subclass can extend a base-class business verb:

```python
@fr.include_view(app)
class PostView(AuthoredBase):
    prefix = "/posts"
    model = Post
    schema = PostRead

    async def create(self, schema_obj):
        # PostView-specific logic before the base class runs
        schema_obj.slug = slugify(schema_obj.title)
        return await super().create(schema_obj)
```

The call chain is `PostView.create` → `AuthoredBase.create` → `AsyncRestView.create`; `handle_create` still wraps it in authorization and the commit bracket.

### Apply router-level dependencies

`dependencies = [Depends(fn)]` applies `fn` to every route without injecting its result. Use it for auth guards or rate limits:

```python
class ProtectedBase(fr.AsyncRestView):
    dependencies = [Depends(require_auth)]


@fr.include_view(app)
class PostView(ProtectedBase):
    prefix = "/posts"
    model = Post
    schema = PostRead
```

Every route on `/posts/` now runs `require_auth` before the endpoint function.

### Share a URL namespace with prefix concatenation

When a base class defines `prefix`, subclass prefixes are appended — an
`ApiV1` base with `prefix = "/api/v1"` puts every subclass under
`/api/v1/...`. The recipe:
[Share Behaviour with Base Views](#prefix-concatenation).

---

## Putting it together

Here is the blog API from Part 1, extended with everything from this tutorial:

```python
import fastapi
import fastapi_restly as fr
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated
from fastapi import Depends
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

fr.configure(async_database_url="sqlite+aiosqlite:///blog.db")


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


@asynccontextmanager
async def lifespan(app: fastapi.FastAPI):
    # Create tables after model classes are declared so they're registered on the metadata.
    await fr.db.async_create_all(fr.DataclassBase)
    yield


app = fastapi.FastAPI(lifespan=lifespan)


# --- Schemas ---

class PostRead(fr.IDSchema):
    title: str
    content: str
    published: bool


class CommentRead(fr.IDSchema):
    content: str
    post_id: fr.IDRef[Post]


# --- Shared base ---

def get_current_user_id(request: fastapi.Request) -> int:
    return request.state.user_id   # set by your auth middleware


class AuthoredBase(fr.AsyncRestView):
    user_id: Annotated[int, Depends(get_current_user_id)]

    async def create(self, schema_obj):
        obj = await self.make_new_object(schema_obj)
        obj.author_id = self.user_id
        return await self.save_object(obj)


# --- Views ---

@fr.include_view(app)
class PostView(AuthoredBase):
    prefix = "/posts"
    model = Post
    schema = PostRead

    async def update(self, obj, schema_obj):
        if obj.published:
            raise fastapi.HTTPException(409, "Cannot edit a published post")
        obj = await self.update_object(obj, schema_obj)
        return await self.save_object(obj)

    async def delete(self, obj):
        obj.deleted_at = datetime.now(timezone.utc)
        await self.session.flush()

    @fr.post("/{id}/publish", status_code=200)
    async def publish(self, id: int):
        post = await self.handle_get_one(id)
        if post.published:
            raise fastapi.HTTPException(409, "Already published")
        async with self.write_action("publish", obj=post):
            post.published = True
        return self.to_response(post)


@fr.include_view(app)
class CommentView(AuthoredBase):
    prefix = "/comments"
    model = Comment
    schema = CommentRead
```

---

## Next steps

- [Override Endpoints](howto_override_endpoints.md) — complete handler reference with all signatures
- [Share Behaviour with Base Views](howto_inheritance.md) — full inheritance guide
- [Testing](howto_testing.md) — test the overrides you write
- [API Reference](api_reference.md)
