# Tutorial: Add Authors and Drafts

This tutorial continues [Build a Blog API](tutorial.md). It turns the blog
into a blog with several writers:

- Anyone can read published posts.
- A new post is a draft. Only its author can see it.
- Only the author can change a post or publish it.
- Comments follow the same rules.

Each rule is one declaration or one short method that Restly calls. The
finished blog does not override any CRUD method. Start from the complete
file at the end of Part 1. This tutorial does not use the `author_token` and
`view_count` demo fields from Part 1.

The examples use {class}`AsyncRestView <fastapi_restly.views.AsyncRestView>`.
The same code works with {class}`RestView <fastapi_restly.views.RestView>`,
the sync variant. Drop the `async` and `await` keywords.

## Know who is calling

Restly does not log users in. Your own authentication decides who is
calling. Restly makes the answer available to the rest of your code through
a `Current` class, a
{class}`fr.ContextNamespace <fastapi_restly.clauses.ContextNamespace>`:

```python
from typing import Annotated

from fastapi import Header


class Current(fr.ContextNamespace):
    user_id: fr.ContextParam[int | None]


def get_user_id(
    user_id: Annotated[int | None, Header(alias="X-User-ID")] = None,
) -> int | None:
    # Demo stand-in for real authentication. None means an anonymous reader.
    return user_id
```

Put this code above the models, because the models will read `Current`.
Then replace the `app = FastAPI(...)` line from Part 1:

```python
app = FastAPI(lifespan=lifespan, dependencies=[Current.depends(user_id=get_user_id)])
```

`get_user_id` takes the user id from an `X-User-ID` header, so you can try
the API with `curl`. Any client can send this header, so use it only for
trying things out. In a real app, this function checks a session cookie or
a token. [Bind from a FastAPI dependency](#current-request-binding) shows
where your own authentication goes.

{meth}`Current.depends(...) <fastapi_restly.clauses.ContextNamespace.depends>`
turns `get_user_id` into a FastAPI dependency. For
each request, it calls `get_user_id` and stores the result in `Current`. All
code that runs during the request can then read it with `Current.user_id()`.
The dependency is on the app, so every route gets it. A reader without the
header gets `None`, because reading the blog does not need a login.

## Record the author

The server decides who wrote a post, not the client. A column default on the
model reads `Current`:

```python
class Post(fr.TimestampsMixin, fr.IDBase):
    title: Mapped[str]
    content: Mapped[str]
    published: Mapped[bool] = mapped_column(default=False)
    author_id: Mapped[int] = mapped_column(init=False, insert_default=Current.user_id)
```

- `insert_default=Current.user_id` sets the column when SQLAlchemy inserts
  the row. Write `Current.user_id` without parentheses. With them, Python
  would read the value once, when it defines the class.
  [Read the current user in a column default](#current-column-defaults)
  explains this.
- `init=False` leaves `author_id` out of the `Post(...)` constructor.
- The default runs on every insert, from any view or script.
- {class}`fr.TimestampsMixin <fastapi_restly.models.TimestampsMixin>` adds
  `created_at` and `updated_at` columns.

The schema returns these fields but does not accept them as input:

```python
class PostSchema(fr.TimestampsSchemaMixin, fr.IDSchema):
    title: str
    content: str
    published: fr.ReadOnly[bool]
    author_id: fr.ReadOnly[int]
```

A {data}`fr.ReadOnly <fastapi_restly.schemas.ReadOnly>` field appears in responses, and Restly ignores it in a
request body. A client that sends `"author_id": 7` does not change the
author. `published` is read-only too. The publish route below is the only way
to change it.
{class}`fr.TimestampsSchemaMixin <fastapi_restly.schemas.TimestampsSchemaMixin>`
adds the two timestamps as read-only fields.

The `post` table has new columns now. `async_create_all` creates missing
tables, but it does not change a table that exists. Delete the `blog.db`
file from Part 1 before you start the app again.

## Hide drafts from other readers

A scope is the rule for which rows a reader can see. Declare it once, on the
model:

```python
from sqlalchemy import or_


class PostClauses(fr.ClauseNamespace):
    model = Post

    default_scope = fr.where_clause(
        or_(Post.published, Post.author_id == Current.user_id)
    )
```

{class}`fr.ClauseNamespace <fastapi_restly.clauses.ClauseNamespace>` holds
the query rules of one model, and
{func}`fr.where_clause <fastapi_restly.clauses.where_clause>` turns a
SQLAlchemy condition into such a rule. The rule named `default_scope` applies
to every read of posts:

- `GET /posts` lists only the posts in the scope, and the total count agrees.
- `GET /posts/{id}` returns 404 for a post outside the scope.
- `PATCH` and `DELETE` load the post the same way, so they also return 404.

Here, `Current.user_id` again has no parentheses. Restly reads the value
each time it runs the query. For an anonymous reader the value is `None`.
`author_id = NULL` is never true in SQL, so an anonymous reader sees only
published posts.

The scope also checks references. Part 1 declared
`post_id: fr.MustExist[int, Post]` on comments. That check looks for the post
inside the scope. So a comment on a draft that you cannot see fails with
404, and this needs no extra code.

Every read of posts now needs a value for `Current.user_id`. A script that
reads posts binds one first, as
[Bind in a script or job](#current-manual-binding) shows. Without a value,
the read fails with an error. It does not return all posts. A view that must
see every post, such as an admin view, declares
`scope =` {data}`fr.clauses.UNSCOPED <fastapi_restly.clauses.UNSCOPED>`. [Scopes](scopes.md) covers this and other
scopes.

## Only the author may change a post

Seeing a post does not mean that you may change it. Another writer can read
a published post, but must not edit it. Restly calls
{meth}`authorize <fastapi_restly.views.RestView.authorize>` before each
action, so the view overrides it:

```python
import fastapi


@fr.include_view(app)
class PostView(fr.AsyncRestView):
    prefix = "/posts"
    model = Post
    schema = PostSchema

    async def authorize(self, action, obj=None, data=None):
        if action in ("get_many", "get_one"):
            return
        if Current.user_id() is None:
            raise fastapi.HTTPException(401, "Log in to write")
        if obj is not None and obj.author_id != Current.user_id():
            raise fr.exc.Forbidden("Only the author can change this")
```

- `action` names the action. The CRUD actions are `get_many`, `get_one`,
  `create`, `update` and `delete`.
- Everyone may read. The scope already decides which posts they see.
- Every write needs a user. Without this check, an anonymous `POST` would
  reach the database without an author and fail there.
- For `update` and `delete`, `obj` is the loaded post. Only its author
  passes.

The two rules give two different answers. A post outside the scope returns
404: for this reader, it does not exist. A refusal in `authorize` returns
403: the post exists, but the reader may not do this.
[`authorize`: gate the action](#authorize-gate) has more examples.

## Publish a draft

Publishing is an action, not a field edit. Add a route for it to `PostView`:

```python
    @fr.post("/{id}/publish", status_code=200)
    async def publish(self, id: int):
        post = await self.get_one(id)
        async with self.write_action("publish", obj=post):
            if post.published:
                raise fr.exc.Conflict("Already published")
            post.published = True
        return self.to_response(post)
```

- {func}`@fr.post <fastapi_restly.views.post>` adds a route to the view,
  beside the CRUD routes.
- {meth}`get_one <fastapi_restly.views.RestView.get_one>` loads the post
  through the scope. Another writer's draft returns 404.
- {meth}`write_action <fastapi_restly.views.RestView.write_action>` first
  calls `authorize("publish", post)`. The `authorize` method above lets only
  the author pass. When the block ends, Restly commits the change. If the
  block raises an exception, Restly does not commit.
- {meth}`to_response <fastapi_restly.views.BaseRestView.to_response>`
  returns the post in the same shape as the CRUD routes.

## Tell subscribers about a new post

Readers want to hear about a new post. Send the message after the commit, so
that subscribers never hear about a post that was not saved:

```python
async def notify_subscribers(post):
    print(f"New post: {post.title}")  # stand-in for an email or a webhook
```

Add this method to `PostView`:

```python
    async def after_action_commit(self, action, new, old=None):
        if action == "publish":
            await notify_subscribers(new)
```

Restly calls
{meth}`after_action_commit <fastapi_restly.views.RestView.after_action_commit>`
after it commits a write. `action` is the name of the action, and `new` is
the saved post. The hook runs after the commit, so an error in it cannot
undo the publish. [When `after_action_commit` raises](#after-action-commit-raises)
shows how to handle such an error. A side effect that must be saved together
with the post, such as an audit row, goes in
{meth}`before_action_commit <fastapi_restly.views.RestView.before_action_commit>`
instead. See [Transaction hooks](#transaction-hooks).

## Share the rules with comments

Comments need the same rules. A comment has an author, and only its author
may edit or delete it. The model and the schema get the same author field as
posts:

```python
class Comment(fr.TimestampsMixin, fr.IDBase):
    content: Mapped[str]
    post_id: Mapped[int] = mapped_column(ForeignKey("post.id"))
    author_id: Mapped[int] = mapped_column(init=False, insert_default=Current.user_id)


class CommentSchema(fr.TimestampsSchemaMixin, fr.IDSchema):
    content: str
    post_id: fr.MustExist[int, Post]
    author_id: fr.ReadOnly[int]
```

Views are normal Python classes. Move `authorize` from `PostView` into a
base class, and let both views inherit it:

```python
class AuthoredView(fr.AsyncRestView):
    async def authorize(self, action, obj=None, data=None):
        ...  # the same method as above


@fr.include_view(app)
class PostView(AuthoredView):
    prefix = "/posts"
    model = Post
    schema = PostSchema
    # publish and after_action_commit as above


@fr.include_view(app)
class CommentView(AuthoredView):
    prefix = "/comments"
    model = Comment
    schema = CommentSchema
```

Register only the views that have a model, not the base class.
[Share Behaviour with Base Views](howto_inheritance.md) covers inheritance in
more detail.

## The complete file

Here is the blog from Part 1 with every change from this tutorial. The same
file is the [blog example](#example-blog), and its tests check each rule
above:

```{literalinclude} ../example-projects/blog/blog/main.py
:language: python
```

## Try it

Run the file with `fastapi dev main.py`. The requests below play two
writers: Anna sends `X-User-ID: 1`, and Ben sends `X-User-ID: 2`.

```bash
# Anna writes a post. It starts as a draft.
curl -X POST http://127.0.0.1:8000/posts \
  -H 'X-User-ID: 1' -H 'Content-Type: application/json' \
  -d '{"title": "Hello", "content": "World"}'
# 201, with "author_id": 1 and "published": false

# Ben cannot see the draft, and cannot comment on it.
curl -H 'X-User-ID: 2' http://127.0.0.1:8000/posts/1
# 404
curl -X POST http://127.0.0.1:8000/comments \
  -H 'X-User-ID: 2' -H 'Content-Type: application/json' \
  -d '{"content": "Nice post", "post_id": 1}'
# 404

# Anna publishes the post. The server terminal prints "New post: Hello".
curl -X POST -H 'X-User-ID: 1' http://127.0.0.1:8000/posts/1/publish
# 200, with "published": true

# Now everyone can read it, also without a header.
curl http://127.0.0.1:8000/posts/1
# 200

# Ben can read the post, but he cannot change it.
curl -X PATCH http://127.0.0.1:8000/posts/1 \
  -H 'X-User-ID: 2' -H 'Content-Type: application/json' \
  -d '{"title": "Edited"}'
# 403, "Only the author can change this"

# The same comment from Ben works now.
curl -X POST http://127.0.0.1:8000/comments \
  -H 'X-User-ID: 2' -H 'Content-Type: application/json' \
  -d '{"content": "Nice post", "post_id": 1}'
# 201, with "author_id": 2
```

## When to override a CRUD method

This blog overrides no CRUD method. Each rule is a declaration, such as a
column default or a scope, or a hook that Restly calls, such as `authorize`.
Try these first.

Override a CRUD method such as `create` when an action needs logic that is
not a rule about a field or a row. For example, `create` on a user view
hashes the password before the user is saved.
[Customizing RestView](#password-hash-example) shows that example. It also
shows which method to override for each kind of change.

## Next steps

These pages cover the topics of this tutorial in more detail:

- [Current context](howto_current.md): bind values in scripts, jobs, and
  tests
- [Scopes](scopes.md): the model scope, view scopes, and a scope for one route
- [Customizing RestView](customize.md): every method you can override
- [Share Behaviour with Base Views](howto_inheritance.md): the full
  inheritance guide
- [Testing](howto_testing.md): test the rules you write
- [API Reference](api_reference.md)
