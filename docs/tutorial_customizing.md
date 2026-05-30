# Tutorial Part 2: Customizing Views

This tutorial extends the blog API from [Part 1](tutorial.md). It introduces the customization system in order, from the simplest override down to shared base classes.

The examples use `AsyncRestView`. The same methods and patterns apply to `RestView` (sync) — just drop the `async`/`await`.

---

## The three tiers of a CRUD verb

Every CRUD verb in Restly is built from three tiers. Knowing which tier to touch is the whole game: pick the highest one that does the job and the lower tiers keep working untouched.

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

- **The framework owns the commit.** `handle_<verb>` runs `before_commit → commit → after_commit` around your business verb, so `after_commit` always runs after the write is durable.
- **The business verb never commits.** Because `create` / `update` / `delete` only build, apply, and save (flush, not commit), you can rewrite them freely without breaking the transaction bracket. The old "mutate-after-save" trap is gone.

Inside every method, `self.session` is the live database session and `self.request` is the FastAPI `Request` object.

---

## Tier 3 — the business verb (the usual override point)

This is where almost all customization lives. The business verb is the domain operation: build an object, apply a payload, save it. It is **auth-free** and **commit-free** — `handle_<verb>` already ran `authorize` before calling it and will run the commit bracket after.

### create — inject server-side fields

Real APIs rarely accept every field from the client. Say each post should record which user created it, taken from the request context rather than from the payload:

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

`make_new_object` builds the ORM instance from the validated payload. `save_object` flushes and refreshes it. Because `create` does not commit, setting a field after `save_object` still persists — the flush happens inside `save_object`, and the commit happens later in `handle_create`. (For a field stamped on *both* create and update, prefer `prepare_create` / `prepare_update`; see [Stamping extra fields](#stamping-extra-fields).)

### update — validate before saving

To block an update based on the current state of the object, override the `update` business verb. It receives the already-loaded object, so there is no separate fetch:

```python
    async def update(self, obj, schema_obj):
        if obj.published:
            raise fastapi.HTTPException(409, "Cannot edit a published post")
        obj = await self.update_object(obj, schema_obj)
        return await self.save_object(obj)
```

`update(obj, schema_obj)` is called by `handle_update`, which has already loaded `obj` (through `get_one`, applying any read scope) and run `authorize`. You only describe the domain change.

### build_query — filter results to the current user

The most common real-world override: restrict reads to rows the caller is allowed to see. `build_query` is the read-scope method that `get_many`, `count`, and `get_one` all consult. A single override keeps listed rows, pagination totals, and single-row fetches in sync — a row hidden from the list returns 404 from `GET /{id}` too, and `update` / `delete` inherit the visibility check because `handle_update` / `handle_delete` load through `get_one`.

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

- **Visibility** — `build_query`. A hidden row simply does not exist for this view, so `get_one` 404s on it for everyone. This stays auth-free.
- **Policy** — `authorize`, called in the request handler. Use it for "may this caller read at all", not for "which rows exist".

### delete — implement soft-delete

The `delete` business verb removes the object. Override it to flip a flag instead:

```python
from datetime import datetime, timezone

    async def delete(self, obj):
        obj.deleted_at = datetime.now(timezone.utc)
        await self.session.flush()
        # Do NOT call super() / delete_object — that would remove the row.
```

`DELETE /posts/{id}` now marks the row instead of removing it. The 204 response is still produced by `delete_endpoint`, and the commit still runs in `handle_delete`; only the domain step changes. Pair this with a `build_query` override that filters out rows where `deleted_at is not None` so soft-deleted rows disappear from reads.

---

## Tier 2 — the request handler (orchestration and timing)

`handle_<verb>` owns `authorize` and the commit bracket. Override it when you need to change *orchestration or timing* without re-declaring the route — for example, to run a delete in a background task, or to wrap the write in a custom transaction. The default implementations look like this:

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

You rarely override `handle_<verb>` itself. The hooks above cover the common cases; reach for a full `handle_<verb>` override only when you need to change the order of operations or the transaction itself.

---

## Stamping extra fields

When the same field must be stamped on **both** create and update — an audit id, a tenant id, an ownership column — override `prepare_create` / `prepare_update` instead of the business verbs. Each returns a dict of *extra* fields to set, and they layer cooperatively, so base classes and mixins compose:

```python
    async def prepare_create(self, schema_obj):
        fields = await super().prepare_create(schema_obj)
        fields["created_by"] = self.request.state.user_id
        return fields
```

`make_new_object` (inside `create`) applies whatever `prepare_create` returns, and `update_object` (inside `update`) applies `prepare_update`. Because they only return extra fields, you do not have to touch the business verb at all.

---

## Object utilities

The business verbs are built from a small set of object utilities. These are **utilities you call**, not override points:

```
create  →  make_new_object(schema_obj)   # build ORM object, run prepare_create
        →  save_object(obj)              # flush + refresh (no commit)

update  →  update_object(obj, schema_obj)  # apply payload, run prepare_update
        →  save_object(obj)

delete  →  delete_object(obj)              # delete + flush (no commit)
```

`make_new_object` and `update_object` do not flush — they prepare the ORM object. `save_object` is the explicit flush/refresh step; it does **not** commit, because `handle_<verb>` owns the commit. The same operations are available as free functions (`fr.make_new_object`, `fr.save_object`, and `async_*` variants) for use in services and workers outside a view.

---

## Custom routes

Use `@fr.get`, `@fr.post`, `@fr.patch`, `@fr.put`, or `@fr.delete` to add endpoints alongside the generated ones. The same three tiers help here: reuse `handle_get_one` (load with scope + 404 + read-auth) or `get_one` (just the scoped load) to fetch, and reuse `save_object` to persist.

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

Calling `self.handle_get_one(id)` gives you the ORM object with the same read scope, 404 logic, and read authorization as the standard `GET /{id}` endpoint — and picks up any `build_query` or `authorize` override you may have applied. (Use the bare `get_one(id)` instead if you want the scoped load and 404 but not the read-auth check.)

### A state-change action

Add a `publish` action that transitions a post to a published state. Load with `handle_get_one`, then run the change through `handle_write` with a custom action name — it applies the same authorize → snapshot → `before_commit` → commit → `after_commit` bracket the CRUD writes use, so you never own the commit by hand:

```python
import fastapi

    @fr.post("/{id}/publish", status_code=200)
    async def publish(self, id: int):
        post = await self.handle_get_one(id)
        if post.published:
            raise fastapi.HTTPException(409, "Already published")

        async def _publish():
            post.published = True
            return await self.save_object(post)

        published = await self.handle_write("publish", obj=post, mutate=_publish)
        return self.to_response_schema(published)
```

`self.to_response_schema(post)` serializes the ORM object using the view's configured response schema, exactly as the standard endpoints do.

If your custom action is really a full create or update — same authorize, same commit bracket, same hooks — call `handle_create` / `handle_update` directly instead of reassembling the pieces:

```python
    @fr.post("/{id}/repost")
    async def repost(self, id: int, schema_obj: PostRead):
        original = await self.handle_get_one(id)
        # ... derive a new payload from `original` ...
        return self.to_response_schema(await self.handle_create(schema_obj))
```

`handle_create` runs `authorize`, your `create` override, and the full `before_commit → commit → after_commit` bracket — so a custom action behaves exactly like the generated `POST /`.

---

## Database conflict responses

By default, Restly translates SQLAlchemy `IntegrityError` exceptions raised by database constraints into `409 Conflict` responses. This is usually what you want for duplicate unique values or invalid foreign-key references, and no handler code is needed in normal CRUD views.

If your app needs a different error envelope, register a handler specifically for `sqlalchemy.exc.IntegrityError` before calling `fr.configure(app=...)` or before registering views with `fr.include_view(app)`:

```python
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

@app.exception_handler(IntegrityError)
async def integrity_error_handler(request, exc):
    return JSONResponse(
        status_code=409,
        content={"error": {"code": "constraint_conflict"}},
    )
```

Restly respects that handler and does not replace it. You can also opt out of the default handler entirely:

```python
fr.configure(app=app, install_default_exception_handlers=False)
```

See [Default Exception Handling](api_reference.md#default-exception-handling) for the exact registration behavior.

---

## Sharing behaviour with base classes

All of the above can be promoted from a single view to a shared base class. Because views are plain Python classes, normal inheritance works without any special framework support.

### Extract authentication into a base class

The blog API has two views that both need a current user. Instead of repeating the dependency and the ownership stamping, override the `create` business verb on a shared base:

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

`self.current_user` is injected by FastAPI's dependency system and is available in every method of every subclass. `AuthoredBase` itself is never passed to `include_view` — only the concrete subclasses are registered.

### Extend a base-class verb with super()

A subclass can extend a base-class business verb rather than replace it:

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

The call chain is `PostView.create` → `AuthoredBase.create` → `AsyncRestView.create`. All three run in order, and the request handler (`handle_create`) still wraps the whole chain in `authorize` and the commit bracket.

### Apply router-level dependencies

`dependencies = [Depends(fn)]` on a view (or base class) applies `fn` to every route the view registers — without the dependency result being injected as an attribute. Use this for authentication guards or rate-limiting:

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

When a base class defines `prefix`, subclass prefixes are appended:

```python
class ApiV1(fr.AsyncRestView):
    prefix = "/api/v1"


@fr.include_view(app)
class PostView(ApiV1):
    prefix = "/posts"     # → /api/v1/posts
    model = Post
    schema = PostRead


@fr.include_view(app)
class CommentView(ApiV1):
    prefix = "/comments"  # → /api/v1/comments
    model = Comment
    schema = CommentRead
```

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
    engine = fr.get_async_engine()
    async with engine.begin() as conn:
        await conn.run_sync(fr.DataclassBase.metadata.create_all)
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

        async def _publish():
            post.published = True
            return await self.save_object(post)

        published = await self.handle_write("publish", obj=post, mutate=_publish)
        return self.to_response_schema(published)


@fr.include_view(app)
class CommentView(AuthoredBase):
    prefix = "/comments"
    model = Comment
    schema = CommentRead
```

---

## Next steps

- [How-To: Override Endpoints](howto_override_endpoints.md) — complete handler reference with all signatures
- [How-To: Share Behaviour with Base Views](howto_inheritance.md) — full inheritance guide
- [How-To: Testing](howto_testing.md) — test the overrides you write
- [API Reference](api_reference.md)
