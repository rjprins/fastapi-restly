# Customize the Blog API

This tutorial extends [Build a Blog API](tutorial.md) from single-method
overrides through shared base classes. Its `author_token` and `view_count` demo
fields are set aside. A shared base class stamps authorship server-side
instead.

The examples use {class}`AsyncRestView <fastapi_restly.views.AsyncRestView>`.
The same methods and patterns apply to {class}`RestView <fastapi_restly.views.RestView>`,
the sync variant. Drop the `async` and `await` keywords.

## The three tiers of a CRUD verb

Before overriding anything, it helps to know where each behavior lives. Every
CRUD verb has three tiers, and the rule is to override the lowest tier that
owns the behavior you need. The full model, including lifecycles and a
decision table, is covered in
[Customizing RestView](customize.md). From the wire
inward, the tiers are:

```
<verb>_endpoint   the endpoint method: the @route, the FastAPI
                  signature/response_model, and to_response. Rarely overridden.
handle_<verb>     the handler: runs authorize and the commit bracket
                  (before_action_commit → commit → after_action_commit), returns the domain
                  object. Override to change orchestration/timing.
<verb>            the business method: the domain operation (build/apply/save).
                  Auth-free and commit-free. The usual override point.
```

The five verbs are {meth}`get_many <fastapi_restly.views.RestView.get_many>`, {meth}`get_one <fastapi_restly.views.RestView.get_one>`, {meth}`create <fastapi_restly.views.RestView.create>`, {meth}`update <fastapi_restly.views.RestView.update>`, and {meth}`delete <fastapi_restly.views.RestView.delete>`. So the full call chain for a create is:

```
POST /     → create_endpoint(schema_obj)     # endpoint method
           → handle_create(schema_obj)       # authorize + commit bracket
           → create(schema_obj)              # build + save, no commit
```

Three facts make this layout safe to override:

- **The handler normally owns the commit.** `handle_<verb>` runs {meth}`before_action_commit <fastapi_restly.views.RestView.before_action_commit>`, then `commit`, then {meth}`after_action_commit <fastapi_restly.views.RestView.after_action_commit>` around the business method.
- **The business method never commits.** `create` / `update` / `delete` build, apply, and flush. The surrounding commit bracket commits later.
- **Several handlers can share one commit.** Inside {ref}`shared_write_action_commit() <shared-write-action-commit>`, a handler returns after its flush. The outermost block commits and runs the queued after-hooks.

Inside every method, `self.session` is the live database session and `self.request` is the FastAPI `Request` object.

## Tier 3: the business method (the usual override point)

Most customization lives here. The business method is the domain operation: build an object, apply a payload, save it. It is auth-free and commit-free; the handler adds authorization and commit handling.

### create: inject server-side fields

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

`make_new_object` builds the ORM instance. `save_object` flushes and refreshes it, then eager-loads the relationships the response schema names, but does not commit. For a field stamped on every write, see [Stamping extra fields](#stamping-extra-fields).

### update: validate before saving

To reject an update based on current state, override {meth}`update <fastapi_restly.views.RestView.update>`. It receives the loaded object:

```python
    async def update(self, obj, schema_obj):
        if obj.published:
            raise fastapi.HTTPException(409, "Cannot edit a published post")
        obj = await self.update_object(obj, schema_obj)
        return await self.save_object(obj)
```

{meth}`handle_update <fastapi_restly.views.RestView.handle_update>` has already loaded `obj` through {meth}`get_one <fastapi_restly.views.RestView.get_one>` and run {meth}`authorize <fastapi_restly.views.RestView.authorize>`, so `update` only describes the domain change.

### The scope: filter results to the current user

The common read customization is row visibility. {meth}`get_many <fastapi_restly.views.RestView.get_many>`, {meth}`count <fastapi_restly.views.RestView.count>`, and {meth}`get_one <fastapi_restly.views.RestView.get_one>` all apply the view's declared {attr}`scope <fastapi_restly.views.BaseRestView.scope>`, so one clause keeps listings, totals, single-row reads, updates, and deletes aligned. Here we restrict every read to the requesting user's own posts:

```python
class Current(fr.ContextNamespace):
    user_id: fr.ContextParam[int]

def get_user_id(request: fastapi.Request) -> int:
    return request.state.user_id

@fr.include_view(app)
class PostView(fr.AsyncRestView):
    prefix = "/posts"
    model = Post
    schema = PostRead
    dependencies = [Current.depends(user_id=get_user_id)]
    scope = fr.where_clause(Post.author_id == Current.user_id)
```

The clause and the context are declared once at module level; the generated dependency binds the per-request value. [Scopes](scopes.md) covers composing clauses and the model-wide `default_scope` form.

Read access has two halves, and they live in two different places:

- **Visibility** belongs to the scope: a hidden row is not part of this view, so `get_one` returns 404.
- **Policy** belongs to {meth}`authorize <fastapi_restly.views.RestView.authorize>`, which is called in the handler. Use it for "may this caller read at all", not for "which rows exist".

### delete: implement soft-delete

The {meth}`delete <fastapi_restly.views.RestView.delete>` business method removes the object. Override it to flip a flag instead:

```python
from datetime import datetime, timezone


class PostView(fr.AsyncRestView):
    prefix = "/posts"
    model = Post
    schema = PostRead

    async def delete(self, obj):
        obj.deleted_at = datetime.now(timezone.utc)
        await self.session.flush()
        # Do NOT call super(); that would remove the row.
```

`DELETE /posts/{id}` now marks the row instead of removing it.
{meth}`delete_endpoint <fastapi_restly.views.RestView.delete_endpoint>` still
returns 204, and {meth}`handle_delete <fastapi_restly.views.RestView.handle_delete>`
still runs the commit bracket. Pair this with a scope clause that hides deleted rows. The canonical recipe lives in [Customizing
RestView](customize.md#delete-soft-delete-instead-of-removing-the-row). The
reusable mixin version is in [Compose Views with
Mixins](howto_compose_views_with_mixins.md).

## Tier 2: the handler (orchestration and timing)

One tier up from the business method sits the handler. `handle_<verb>` owns {meth}`authorize <fastapi_restly.views.RestView.authorize>` and the commit bracket. Override it to change *orchestration or timing* without re-declaring the route. Outside a shared commit block, the defaults look like this:

```
handle_create  →  authorize("create", data=schema_obj)
               →  create(schema_obj)
               →  before_action_commit → commit → after_action_commit

handle_update  →  get_one(id)                     # loads through the scope
               →  authorize("update", obj, data=schema_obj)
               →  update(obj, schema_obj)
               →  before_action_commit → commit → after_action_commit

handle_delete  →  get_one(id)
               →  authorize("delete", obj)
               →  delete(obj)
               →  before_action_commit → commit → after_action_commit
```

The transaction hooks are the usual reason to drop to this tier:

- {meth}`before_action_commit(action, new, old=None) <fastapi_restly.views.RestView.before_action_commit>` runs an in-transaction side effect (an outbox row, an audit row) that commits atomically with the write.
- {meth}`after_action_commit(action, new, old=None) <fastapi_restly.views.RestView.after_action_commit>` runs a post-commit side effect (an email, a webhook, a cache invalidation) only after the write is durable.

Both receive `old`, the pre-mutation snapshot produced by {meth}`snapshot(obj) <fastapi_restly.views.BaseRestView.snapshot>`, so you can fire only on a real change:

```python
    async def after_action_commit(self, action, new, old=None):
        if action == "update" and old["published"] != new.published:
            await notify_subscribers(new.id)
```

The hooks cover most timing needs. Override `handle_<verb>` only when the operation order or transaction must change.

## Stamping extra fields

The `create` override earlier stamped a field in the verb, which covers that verb only. A field the server owns on every write, created or updated by any view, helper, or script, is a column default on the model, reading a per-request context slot:

```python
class Current(fr.ContextNamespace):
    user_id: fr.ContextParam[int | None]


class Post(fr.TimestampsMixin, fr.IDBase):
    title: Mapped[str]
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id"), default=None, insert_default=lambda: Current.user_id()
    )
    updated_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id"),
        default=None,
        insert_default=lambda: Current.user_id(),
        onupdate=lambda: Current.user_id(),
    )
```

A `Current.depends(user_id=...)` entry in the view's `dependencies` binds the slot per request. Mark the fields `fr.ReadOnly` on the schema, so no payload value competes with the default. [Compose Views with Mixins](howto_compose_views_with_mixins.md) has the tenant and soft-delete pieces.

## Object utilities

The business methods are built from a small set of object utilities that you call, never override:

```
create  →  make_new_object(schema_obj)   # build ORM object (no flush)
        →  save_object(obj)              # flush + refresh + eager-load (no commit)

update  →  update_object(obj, schema_obj)  # apply payload (no flush)
        →  save_object(obj)

delete  →  removes the row + flush         # no utility: override delete itself for a soft delete
```

`make_new_object` and `update_object` do not flush. `save_object` flushes, refreshes, and eager-loads the relationships the response schema names, but does *not* commit. The same operations are available as free functions for services and workers; the free `save_object` has no view to read a schema from, so it flushes and refreshes only.

## Custom routes

Views are not limited to the default CRUD methods. Use {func}`@fr.get <fastapi_restly.views.get>`, {func}`@fr.post <fastapi_restly.views.post>`, {func}`@fr.patch <fastapi_restly.views.patch>`, {func}`@fr.put <fastapi_restly.views.put>`, or {func}`@fr.delete <fastapi_restly.views.delete>` to add endpoints. Reuse {meth}`handle_get_one <fastapi_restly.views.RestView.handle_get_one>` for a scoped load with read authorization on a read route, {meth}`get_one <fastapi_restly.views.RestView.get_one>` for a scoped load only, and `save_object` to persist.

All route decorator keyword arguments are passed through to FastAPI, so you configure class-based routes the same way you configure regular FastAPI routes: use `response_model=`, `status_code=`, `dependencies=`, `responses=`, and the other FastAPI route options as usual.

### A computed read endpoint

First we expose a summary of a post without returning the full record:

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

{meth}`handle_get_one(id) <fastapi_restly.views.RestView.handle_get_one>` gives the same scope, 404 behavior, and read authorization as `GET /{id}`. Use {meth}`get_one(id) <fastapi_restly.views.RestView.get_one>` when you want scope and 404 without read authorization.

### A state-change action

Next we add a `publish` action. Load with {meth}`get_one <fastapi_restly.views.RestView.get_one>`, then use {meth}`write_action <fastapi_restly.views.RestView.write_action>` so authorization, snapshot, commit hooks, and commit stay in the framework bracket. The bracket authorizes `"publish"` itself, so the load carries no read gate; the built-in write handlers load the same way:

```python
import fastapi

    @fr.post("/{id}/publish", status_code=200)
    async def publish(self, id: int):
        post = await self.get_one(id)
        if post.published:
            raise fastapi.HTTPException(409, "Already published")
        async with self.write_action("publish", obj=post):
            post.published = True
        return self.to_response(post)
```

{meth}`self.to_response(post) <fastapi_restly.views.BaseRestView.to_response>` serializes through the view's response schema, the
same way the inherited CRUD endpoint methods do.

If a custom action is just a create or update under another URL, call {meth}`handle_create <fastapi_restly.views.RestView.handle_create>` / {meth}`handle_update <fastapi_restly.views.RestView.handle_update>`:

```python
    @fr.post("/{id}/repost")
    async def repost(self, id: int, schema_obj: PostRead):
        original = await self.get_one(id)
        # ... derive a new payload from `original` ...
        return self.to_response(await self.handle_create(schema_obj))
```

`handle_create` runs authorization, your {meth}`create <fastapi_restly.views.RestView.create>` override, and the commit bracket.

## Database conflict responses

Writes can also violate database constraints. Restly turns SQLAlchemy
`IntegrityError` exceptions into `409 Conflict` responses by default; custom
envelopes and the opt-out are covered in
[Default Exception Handling](api_reference.md#default-exception-handling).

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

A subclass can extend a base-class business method:

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

The call passes from `PostView.create` through `AuthoredBase.create` to {meth}`AsyncRestView.create <fastapi_restly.views.AsyncRestView.create>`, and {meth}`handle_create <fastapi_restly.views.RestView.handle_create>` still wraps the whole chain in authorization and the commit bracket.

### Apply router-level dependencies

{attr}`dependencies = [Depends(fn)] <fastapi_restly.views.View.dependencies>` applies `fn` to every route without injecting its result. Use it for auth guards or rate limits:

```python
class ProtectedBase(fr.AsyncRestView):
    dependencies = [Depends(require_auth)]


@fr.include_view(app)
class PostView(ProtectedBase):
    prefix = "/posts"
    model = Post
    schema = PostRead
```

Every route on `/posts` now runs `require_auth` before the endpoint function.

### Share a URL namespace with prefix concatenation

When a base class defines {attr}`prefix <fastapi_restly.views.View.prefix>`, subclass prefixes are appended: an
`ApiV1` base with `prefix = "/api/v1"` puts every subclass under
`/api/v1/...`. The full recipe is in
[Share Behaviour with Base Views](howto_inheritance.md#concatenate-url-prefixes).

## Putting it together

Here is the blog API from [Build a Blog API](tutorial.md), extended with the
customizations from this tutorial. A three-line middleware stands in for real
authentication so the file runs as shown:

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
    await fr.db.async_create_all(fr.IDBase)
    yield


app = fastapi.FastAPI(lifespan=lifespan)


@app.middleware("http")
async def fake_auth(request, call_next):
    request.state.user_id = 1   # demo stand-in for your real auth
    return await call_next(request)


# --- Schemas ---

class PostRead(fr.IDSchema):
    title: str
    content: str
    published: bool


class CommentRead(fr.IDSchema):
    content: str
    post_id: fr.MustExist[int, Post]


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
        post = await self.get_one(id)
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

## Try it

Run the file with `fastapi dev main.py`, then exercise the customized
behaviour:

```bash
curl -X POST http://127.0.0.1:8000/posts \
  -H 'Content-Type: application/json' \
  -d '{"title": "Hello", "content": "World", "published": false}'
# 201; the server stamps author_id on the new row

curl -X POST http://127.0.0.1:8000/posts/1/publish
# 200 with "published": true; a second call returns 409 "Already published"

curl -X PATCH http://127.0.0.1:8000/posts/1 \
  -H 'Content-Type: application/json' -d '{"title": "Edited"}'
# 409 "Cannot edit a published post"; the update override rejects it

curl -X DELETE http://127.0.0.1:8000/posts/1
# 204; the delete override sets deleted_at instead of removing the row
```

A follow-up `GET /posts/1` still returns the post, because nothing filters
soft-deleted rows yet; hiding them is the scope pairing described in the
soft-delete section above.

## Next steps

These pages cover the patterns from this tutorial in more detail:

- [Customizing RestView](customize.md): the complete override reference with all recipes
- [Share Behaviour with Base Views](howto_inheritance.md): the full inheritance guide
- [Testing](howto_testing.md): test the overrides you write
- [API Reference](api_reference.md)
