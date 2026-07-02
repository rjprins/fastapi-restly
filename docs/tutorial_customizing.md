# Tutorial Part 2: Customizing Views

In Part 2 we extend the blog API from [Part 1](tutorial.md), working through
customization from single-method overrides to shared base classes. Part 1's
`author_token` and `view_count` demo fields are set aside in this part; a
shared base class stamps authorship server-side instead.

The examples use {class}`AsyncRestView <fastapi_restly.views.AsyncRestView>`. The same methods and patterns apply to {class}`RestView <fastapi_restly.views.RestView>`, the sync variant; simply drop the `async`/`await`.

## The three tiers of a CRUD verb

Before overriding anything, it helps to know where each behavior lives. Every
CRUD verb has three tiers, and the rule is to override the lowest tier that
owns the behavior you need. The full model, including lifecycles and a
decision table, is covered in
[Customize RestView](customize.md). From the wire
inward, the tiers are:

```
<verb>_endpoint   the route shell (wire boundary): the @route, the FastAPI
                  signature/response_model, and to_response. Rarely overridden.
handle_<verb>     the request handler: runs authorize and the commit bracket
                  (before_commit → commit → after_commit), returns the domain
                  object. Override to change orchestration/timing.
<verb>            the business verb: the domain operation (build/apply/save).
                  Auth-free and commit-free. The usual override point.
```

The five verbs are {meth}`get_many <fastapi_restly.views.RestView.get_many>`, {meth}`get_one <fastapi_restly.views.RestView.get_one>`, {meth}`create <fastapi_restly.views.RestView.create>`, {meth}`update <fastapi_restly.views.RestView.update>`, and {meth}`delete <fastapi_restly.views.RestView.delete>`. So the full call chain for a create is:

```
POST /     → create_endpoint(schema_obj)     # route shell
           → handle_create(schema_obj)       # authorize + commit bracket
           → create(schema_obj)              # build + save, no commit
```

Two facts make this layout safe to override:

- **The handler owns the commit.** `handle_<verb>` runs {meth}`before_commit <fastapi_restly.views.RestView.before_commit>`, then `commit`, then {meth}`after_commit <fastapi_restly.views.RestView.after_commit>` around the business verb.
- **The business verb never commits.** `create` / `update` / `delete` build, apply, and flush. The handler commits later.

Inside every method, `self.session` is the live database session and `self.request` is the FastAPI `Request` object.

## Tier 3: the business verb (the usual override point)

Most customization lives here. The business verb is the domain operation: build an object, apply a payload, save it. It is auth-free and commit-free; the handler adds authorization and commit handling.

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

`make_new_object` builds the ORM instance. `save_object` flushes and refreshes it, but does not commit. For fields stamped on both create and update, override `make_new_object` / `update_object` instead; see [Stamping extra fields](#stamping-extra-fields).

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

### build_query: filter results to the current user

The common read override is row visibility. {meth}`get_many <fastapi_restly.views.RestView.get_many>`, {meth}`count <fastapi_restly.views.RestView.count>`, and {meth}`get_one <fastapi_restly.views.RestView.get_one>` all use {meth}`build_query <fastapi_restly.views.RestView.build_query>`, so one filter keeps listings, totals, single-row reads, updates, and deletes aligned. Here we restrict every read to the requesting user's own posts:

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

- **Visibility** belongs to `build_query`: a hidden row is not part of this view, so `get_one` returns 404.
- **Policy** belongs to {meth}`authorize <fastapi_restly.views.RestView.authorize>`, which is called in the request handler. Use it for "may this caller read at all", not for "which rows exist".

### delete: implement soft-delete

The {meth}`delete <fastapi_restly.views.RestView.delete>` business verb removes the object. Override it to flip a flag instead:

```python
from datetime import datetime, timezone


class PostView(fr.AsyncRestView):
    prefix = "/posts"
    model = Post
    schema = PostRead

    async def delete(self, obj):
        obj.deleted_at = datetime.now(timezone.utc)
        await self.session.flush()
        # Do NOT call super() / delete_object; that would remove the row.
```

`DELETE /posts/{id}` now marks the row instead of removing it. {meth}`delete_endpoint <fastapi_restly.views.RestView.delete_endpoint>` still returns 204, and {meth}`handle_delete <fastapi_restly.views.RestView.handle_delete>` still commits. Pair this with a {meth}`build_query <fastapi_restly.views.RestView.build_query>` filter that hides deleted rows; the canonical recipe lives in [Customize RestView](customize.md#delete-soft-delete-instead-of-removing-the-row), and the reusable mixin version in [Compose Views with Mixins](howto_compose_views_with_mixins.md).

## Tier 2: the request handler (orchestration and timing)

One tier up from the business verb sits the request handler. `handle_<verb>` owns {meth}`authorize <fastapi_restly.views.RestView.authorize>` and the commit bracket; override it to change *orchestration or timing* without re-declaring the route. The defaults look like this:

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

- {meth}`before_commit(action, new, old=None) <fastapi_restly.views.RestView.before_commit>` runs an in-transaction side effect (an outbox row, an audit row) that commits atomically with the write.
- {meth}`after_commit(action, new, old=None) <fastapi_restly.views.RestView.after_commit>` runs a post-commit side effect (an email, a webhook, a cache invalidation) only after the write is durable.

Both receive `old`, the pre-mutation snapshot produced by {meth}`snapshot(obj) <fastapi_restly.views.BaseRestView.snapshot>`, so you can fire only on a real change:

```python
    async def after_commit(self, action, new, old=None):
        if action == "update" and old["published"] != new.published:
            await notify_subscribers(new.id)
```

The hooks cover most timing needs. Override `handle_<verb>` only when the operation order or transaction must change.

## Stamping extra fields

The `create` override earlier stamped a field at creation time only. For fields stamped on both create and update, override `make_new_object` / `update_object` cooperatively: call `super()`, mutate, and return. Base classes and mixins then compose cleanly:

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

## Object utilities

The business verbs are built from a small set of object utilities. `save_object` and `delete_object` you only ever call; `make_new_object` and `update_object` you call as well, but they double as the cooperative override points from the previous section:

```
create  →  make_new_object(schema_obj)   # build ORM object (override point for stamping)
        →  save_object(obj)              # flush + refresh (no commit)

update  →  update_object(obj, schema_obj)  # apply payload (override point for stamping)
        →  save_object(obj)

delete  →  delete_object(obj)              # delete + flush (no commit)
```

`make_new_object` and `update_object` do not flush. `save_object` flushes and refreshes, but does *not* commit. The same operations are available as free functions for services and workers.

## Custom routes

Views are not limited to the generated verbs. Use {func}`@fr.get <fastapi_restly.views.get>`, {func}`@fr.post <fastapi_restly.views.post>`, {func}`@fr.patch <fastapi_restly.views.patch>`, {func}`@fr.put <fastapi_restly.views.put>`, or {func}`@fr.delete <fastapi_restly.views.delete>` to add endpoints. Reuse {meth}`handle_get_one <fastapi_restly.views.RestView.handle_get_one>` for a scoped load with read authorization, {meth}`get_one <fastapi_restly.views.RestView.get_one>` for a scoped load only, and `save_object` to persist.

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

Next we add a `publish` action. Load with {meth}`handle_get_one <fastapi_restly.views.RestView.handle_get_one>`, then use {meth}`write_action <fastapi_restly.views.RestView.write_action>` so authorization, snapshot, commit hooks, and commit stay in the framework bracket:

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

{meth}`self.to_response(post) <fastapi_restly.views.BaseRestView.to_response>` serializes through the view's response schema, the
same way the generated routes do.

If a custom action is just a create or update under another URL, call {meth}`handle_create <fastapi_restly.views.RestView.handle_create>` / {meth}`handle_update <fastapi_restly.views.RestView.handle_update>`:

```python
    @fr.post("/{id}/repost")
    async def repost(self, id: int, schema_obj: PostRead):
        original = await self.handle_get_one(id)
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

Every route on `/posts/` now runs `require_auth` before the endpoint function.

### Share a URL namespace with prefix concatenation

When a base class defines {attr}`prefix <fastapi_restly.views.View.prefix>`, subclass prefixes are appended: an
`ApiV1` base with `prefix = "/api/v1"` puts every subclass under
`/api/v1/...`. The full recipe is in
[Share Behaviour with Base Views](howto_inheritance.md#concatenate-url-prefixes).

## Putting it together

Here is the blog API from Part 1, extended with the customizations from this
part. A three-line middleware stands in for real authentication so the file
runs as is:

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

## Try it

Run the file with `fastapi dev main.py`, then exercise the customized
behaviour:

```bash
curl -X POST http://127.0.0.1:8000/posts/ \
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
soft-deleted rows yet; hiding them is the `build_query` pairing described in
the soft-delete section above.

## Next steps

The pages below go deeper into the patterns from this part:

- [Customize RestView](customize.md): the complete override reference with all recipes
- [Share Behaviour with Base Views](howto_inheritance.md): the full inheritance guide
- [Testing](howto_testing.md): test the overrides you write
- [API Reference](api_reference.md)
