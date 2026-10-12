# Tutorial: Add Authors and Drafts

This tutorial continues [Build a Blog API](tutorial.md). It turns the blog
into a blog with several writers:

- Anyone can read published posts.
- A new post is a draft. Only its author can see it.
- Only the author can change a post or publish it.
- Comments follow the same rules.

Each rule is one declaration or one short method that Restly calls. At the
end, you write tests for the rules. The tutorial takes about forty minutes. Start
from the complete file of [Build a Blog API](tutorial.md).

## Know who is calling

Every rule above depends on who is calling. Restly does not log users in.
Your own authentication decides who is calling. Restly makes the answer
available to the rest of your code through a `Current` class, a
{class}`fr.ContextNamespace <fastapi_restly.clauses.ContextNamespace>`.

Add `from typing import Annotated` and `from fastapi import Header` to the
imports. Then put this code above the `app = FastAPI(...)` line:

```{literalinclude} ../example-projects/blog/blog/main.py
:language: python
:start-at: class Current(fr.ContextNamespace):
:end-at: return user_id
```

Replace the `app = FastAPI(...)` line with this one:

```{literalinclude} ../example-projects/blog/blog/main.py
:language: python
:start-at: app = FastAPI(
:end-at: get_user_id)])
```

`get_user_id` takes the user id from an `X-User-ID` header, so you can try
the API with `curl`. Any client can send this header, so use it only for
trying things out. In a real app, this function checks a session cookie or
a token. [Bind from a FastAPI dependency](#current-request-binding) shows
where your own authentication goes.

{meth}`Current.depends(...) <fastapi_restly.clauses.ContextNamespace.depends>`
turns `get_user_id` into a FastAPI dependency. For each request, it calls
`get_user_id` and stores the result in `Current`. All code that runs during
the request can then read it with `Current.user_id()`. The dependency is on
the app, so every route gets it. A reader without the header gets `None`,
because reading the blog does not need a login.

Reload <http://127.0.0.1:8000/docs>. Every route now has an `X-User-ID`
header field. The requests in this tutorial play two writers: Anna sends
`X-User-ID: 1`, and Ben sends `X-User-ID: 2`.

## Record the author

The server decides who wrote a post, not the client. A column default on the
model reads `Current`. Add the `author_id` column to `Post`:

```{literalinclude} ../example-projects/blog/blog/main.py
:language: python
:pyobject: Post
```

`insert_default=Current.user_id` sets the column when SQLAlchemy inserts the
row. Write `Current.user_id` without parentheses. With them, Python would
read the value when it defines the class. No request has set a value then,
so that fails.
[Read the current user in a column default](#current-column-defaults)
explains this. The default runs on every insert, from any view or script.

The `post` table has a new column now.
{func}`async_create_all <fastapi_restly.db.async_create_all>` creates missing
tables, but it does not change a table that exists. Stop the server with
Ctrl+C, delete the `blog.db` file, and start the server again with
`fastapi dev main.py`. It creates the tables again. Then let Anna write a
post. She also sends `"author_id": 2`:

```bash
curl -X POST http://127.0.0.1:8000/posts \
  -H 'X-User-ID: 1' -H 'Content-Type: application/json' \
  -d '{"title": "Hello", "content": "World", "author_id": 2}'
# 201, with "author_id": 2
```

Anna has written a post in Ben's name. Posts still use the schema that Restly
generated from the model, and that schema accepts every column. So posts need
a schema of their own, which returns `author_id` but does not accept it.
Delete the post first:

```bash
curl -X DELETE http://127.0.0.1:8000/posts/1
```

Add this schema above `PostView`, and add `schema = PostSchema` to the view,
as you did for comments:

```{literalinclude} ../example-projects/blog/blog/main.py
:language: python
:pyobject: PostSchema
```

A {data}`fr.ReadOnly <fastapi_restly.schemas.ReadOnly>` field appears in
responses, and Restly ignores it in a request body. `published` is read-only
too, because a new post is a draft. A route later in this tutorial publishes
a post. Send Anna's request again:

```bash
curl -X POST http://127.0.0.1:8000/posts \
  -H 'X-User-ID: 1' -H 'Content-Type: application/json' \
  -d '{"title": "Hello", "content": "World", "author_id": 2}'
# 201, with "author_id": 1 and "published": false
```

## Hide drafts from other readers

A scope is the rule for which rows a reader can see. Declare it once, for the
model. Add `or_` to the `sqlalchemy` import, and put this below `Post`:

```{literalinclude} ../example-projects/blog/blog/main.py
:language: python
:pyobject: PostClauses
```

{class}`fr.ClauseNamespace <fastapi_restly.clauses.ClauseNamespace>` holds
the query rules of one model, and
{func}`fr.where_clause <fastapi_restly.clauses.where_clause>` turns a
SQLAlchemy condition into such a rule. The rule named `default_scope` applies
to every read of posts. The list and its total count show only the posts in
the scope. A single post outside the scope returns 404, also for `PATCH` and
`DELETE`.

Here, `Current.user_id` again has no parentheses. Restly reads the value
each time it runs the query. For an anonymous reader the value is `None`.
`author_id = NULL` is never true in SQL, so an anonymous reader sees only
published posts. Try it as Ben, as an anonymous reader, and as Anna:

```bash
curl -H 'X-User-ID: 2' http://127.0.0.1:8000/posts/1
# 404
curl http://127.0.0.1:8000/posts
# 200, with "total_count": 0
curl -H 'X-User-ID: 1' http://127.0.0.1:8000/posts/1
# 200
```

The scope also checks references. `post_id: fr.MustExist[int, Post]` on
comments looks for the post inside the scope. So Ben cannot comment on
Anna's draft, and this needs no extra code:

```bash
curl -X POST http://127.0.0.1:8000/comments \
  -H 'X-User-ID: 2' -H 'Content-Type: application/json' \
  -d '{"content": "Nice post", "post_id": 1}'
# 404
```

## Only the author may change a post

Writing needs a user. Try to write a post without the header:

```bash
curl -X POST http://127.0.0.1:8000/posts \
  -H 'Content-Type: application/json' \
  -d '{"title": "Hi", "content": "Anonymous"}'
# 409, "Not-null constraint violated on post.author_id"
```

The post has no author, so the database refuses it. The answer should come
before the database, and say what is wrong. And seeing a post does not mean
that you may change it: another writer can read a published post, but must
not edit it. Restly calls
{meth}`authorize <fastapi_restly.views.AsyncRestView.authorize>` before each
action, so the view overrides it. Add `import fastapi` to the imports, and
add `authorize` to `PostView`:

```python
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
        if action == "create":
            return
        if obj is None or obj.author_id != Current.user_id():
            raise fr.exc.Forbidden("Only the author can change this")
```

- `action` names the action. The CRUD actions are `get_many`, `get_one`,
  `create`, `update` and `delete`.
- Everyone may read. The scope already decides which posts they see.
- Every write needs a user, and every user may create a post.
- Every other write changes one post, and `obj` is that post. Only its
  author passes. A write without `obj` fails, so a new action is safe until
  you allow it.

Send the anonymous request again, and let Ben try to edit Anna's draft:

```bash
curl -X POST http://127.0.0.1:8000/posts \
  -H 'Content-Type: application/json' \
  -d '{"title": "Hi", "content": "Anonymous"}'
# 401, "Log in to write"
curl -X PATCH http://127.0.0.1:8000/posts/1 \
  -H 'X-User-ID: 2' -H 'Content-Type: application/json' \
  -d '{"title": "Edited"}'
# 404
```

Ben gets 404, because the scope hides the draft from him: for this reader,
the post does not exist. When he can see the post but may not change it,
`authorize` answers 403. The next section shows this.
[Allow or refuse an action](#authorize-gate) has more examples.

## Publish a draft

Publishing is an action, not a field edit. Add a route for it to `PostView`:

```{literalinclude} ../example-projects/blog/blog/main.py
:language: python
:pyobject: PostView.publish
```

- {func}`@fr.post <fastapi_restly.views.post>` adds a route to the view,
  beside the CRUD routes.
- {meth}`get_one <fastapi_restly.views.AsyncRestView.get_one>` loads the post
  through the scope. Another writer's draft returns 404.
- {meth}`write_action <fastapi_restly.views.AsyncRestView.write_action>` first
  calls `authorize("publish", post)`. The `authorize` method above lets only
  the author pass. When the block ends, Restly commits the change. If the
  block raises an exception, Restly does not commit.
- {meth}`to_response <fastapi_restly.views.BaseRestView.to_response>`
  returns the post in the same shape as the CRUD routes.

Anna publishes her post. Then everyone can read it, and Ben gets 403 when he
tries to edit it:

```bash
curl -X POST -H 'X-User-ID: 1' http://127.0.0.1:8000/posts/1/publish
# 200, with "published": true
curl http://127.0.0.1:8000/posts/1
# 200
curl -X PATCH http://127.0.0.1:8000/posts/1 \
  -H 'X-User-ID: 2' -H 'Content-Type: application/json' \
  -d '{"title": "Edited"}'
# 403, "Only the author can change this"
curl -X POST -H 'X-User-ID: 1' http://127.0.0.1:8000/posts/1/publish
# 409, "Already published"
```

## Tell subscribers about a new post

Readers want to hear about a new post. Send the message after the commit, so
that subscribers never hear about a post that was not saved. Put this
function above `PostView`:

```{literalinclude} ../example-projects/blog/blog/main.py
:language: python
:pyobject: notify_subscribers
```

Add this method to `PostView`:

```{literalinclude} ../example-projects/blog/blog/main.py
:language: python
:pyobject: PostView.after_action_commit
```

Restly calls
{meth}`after_action_commit <fastapi_restly.views.AsyncRestView.after_action_commit>`
after it commits a write. `action` is the name of the action, and `new` is
the saved post. The hook runs after the commit, so an error in it cannot
undo the publish. [When `after_action_commit` raises](#after-action-commit-raises)
shows how to handle such an error.

Anna writes a second post and publishes it. The server terminal prints
`New post: Second`:

```bash
curl -X POST http://127.0.0.1:8000/posts \
  -H 'X-User-ID: 1' -H 'Content-Type: application/json' \
  -d '{"title": "Second", "content": "More"}'
curl -X POST -H 'X-User-ID: 1' http://127.0.0.1:8000/posts/2/publish
```

A side effect that must be saved together with the post, such as an audit
row, goes in
{meth}`before_action_commit <fastapi_restly.views.AsyncRestView.before_action_commit>`
instead. See [Run code before or after the commit](#transaction-hooks).

## Share the rules with comments

Comments need the same rules:

- A comment has an author.
- A comment on a hidden draft stays hidden.
- Only its author may edit or delete a comment.

The model and the schema get the same author field as posts. The model also
gets a scope. Add `relationship` to the `sqlalchemy.orm` import, and replace
`Comment` and `CommentSchema` with this code:

```{literalinclude} ../example-projects/blog/blog/main.py
:language: python
:start-at: class Comment(Base):
:end-at: author_id: fr.ReadOnly[int]
```

A scope covers only its own model. The post scope hides a draft, but not the
comments on it. So comments get their own scope:

- `post` is a relationship from a comment to its post.
- `Comment.post.has(...)` is true when the comment's post matches the
  condition in the brackets.
- The condition is the post scope, written without parentheses. Restly reads
  its `Current.user_id` each time it applies the comment scope.

Together: a comment is visible when its post is visible.
[Use a clause inside another condition](#clause-in-expression) explains this in more
detail.

Views are normal Python classes. Move `authorize` from `PostView` into a
base class, and put the base class below the `app = FastAPI(...)` line:

```{literalinclude} ../example-projects/blog/blog/main.py
:language: python
:pyobject: AuthoredView
```

Both views inherit it:

```python
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
[Share behavior with base views](#share-behaviour-with-base-views) covers inheritance in
more detail.

The `comment` table has a new column. Stop the server, delete `blog.db`, and
start the server again. Then try the whole story again, with comments:

```bash
# Anna writes a post, and a note on her draft.
curl -X POST http://127.0.0.1:8000/posts \
  -H 'X-User-ID: 1' -H 'Content-Type: application/json' \
  -d '{"title": "Hello", "content": "World"}'
curl -X POST http://127.0.0.1:8000/comments \
  -H 'X-User-ID: 1' -H 'Content-Type: application/json' \
  -d '{"content": "Note to self", "post_id": 1}'
# 201, with "author_id": 1

# Ben does not see the note, because the post is a draft.
curl -H 'X-User-ID: 2' http://127.0.0.1:8000/comments
# 200, with "total_count": 0

# Anna publishes the post. Now Ben can comment on it.
curl -X POST -H 'X-User-ID: 1' http://127.0.0.1:8000/posts/1/publish
curl -X POST http://127.0.0.1:8000/comments \
  -H 'X-User-ID: 2' -H 'Content-Type: application/json' \
  -d '{"content": "Nice post", "post_id": 1}'
# 201, with "author_id": 2

# Anna cannot change Ben's comment.
curl -X PATCH http://127.0.0.1:8000/comments/2 \
  -H 'X-User-ID: 1' -H 'Content-Type: application/json' \
  -d '{"content": "Edited"}'
# 403, "Only the author can change this"
```

## Test the rules

The requests above check each rule by hand. Tests check them after every
change. Install Restly's test tools:

```bash
pip install "fastapi-restly[testing]"
```

Tests need their own database. Add `import os` to the imports, and let an
environment variable choose the database:

```python
fr.configure(
    async_database_url=os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///blog.db")
)
```

Create `conftest.py` next to `main.py`. It sets the variable before it
imports the app, because `main.py` configures Restly when it is imported. The
tests use a database in memory, so each test run creates new tables:

```python
import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite://"

import fastapi_restly as fr  # noqa: E402
from main import Base, app  # noqa: E402

fr.testing.configure_tests(app=app, base=Base, create_all=True)
```

{func}`configure_tests <fastapi_restly.testing.configure_tests>` creates the
tables in the test database, and gives each test a `restly_client` fixture.
Restly rolls back everything a test writes when the test ends, so each test
starts from an empty blog. Create `test_main.py`:

```python
ANNA = {"X-User-ID": "1"}
BEN = {"X-User-ID": "2"}


def test_only_the_author_sees_a_draft(restly_client):
    post = restly_client.post(
        "/posts", json={"title": "Hello", "content": "World"}, headers=ANNA
    ).json()

    restly_client.get(f"/posts/{post['id']}", headers=ANNA)
    restly_client.get(f"/posts/{post['id']}", headers=BEN, assert_status_code=404)


def test_only_the_author_changes_a_published_post(restly_client):
    post = restly_client.post(
        "/posts", json={"title": "Hello", "content": "World"}, headers=ANNA
    ).json()
    restly_client.post(f"/posts/{post['id']}/publish", headers=ANNA)

    restly_client.patch(
        f"/posts/{post['id']}", json={"title": "Edited"}, headers=BEN, assert_status_code=403
    )
    restly_client.patch(f"/posts/{post['id']}", json={"title": "Edited"}, headers=ANNA)
```

The client checks the status code of every response. Without
`assert_status_code`, it expects a status in the 2xx range. Run the tests:

```bash
pytest
```

```text
test_main.py ..                                              [100%]
2 passed
```

The [blog example](https://github.com/rjprins/fastapi-restly/tree/main/example-projects/blog) has a test for every rule in this
tutorial. [Testing](howto_testing.md) covers the fixtures, and apps that
build their configuration in a factory.

## The complete file

Here is the blog with every change from this tutorial. The same file is the
[blog example](https://github.com/rjprins/fastapi-restly/tree/main/example-projects/blog):

```{literalinclude} ../example-projects/blog/blog/main.py
:language: python
```

## Exercises

Write a test for each exercise.

1. Add `POST /posts/{id}/unpublish`. Only the author may call it, and it
   fails with 409 on a draft.
2. Record when a post was published. Add a `published_at` column, set it in
   the publish route, and return it as a read-only field.
3. Let the author of a post delete any comment on it. Override `authorize`
   in `CommentView`, and call `super().authorize(...)` for the other cases.
   Load the post with `await self.session.get(Post, obj.post_id)`, because
   `obj.post` does not load by itself in async code.
   [Return related objects in a response](#relationship-loading) explains
   why.

## Next steps

These pages cover the topics of this tutorial in more detail:

- [Current & ContextParam](howto_current.md): bind values in scripts, jobs, and
  tests
- [Scopes](scopes.md): the model scope, view scopes, and a scope for one route
- [Request Lifecycle & Hooks](customize.md): every method you can override, and
  when to override one
- [Share behavior with base views](#share-behaviour-with-base-views): the full
  inheritance guide
- [Testing](howto_testing.md): the test fixtures, and test databases
- [New & Existing Projects](projects.md): split the file into
  packages as the app grows, or generate a project with `restly new`
- [Deploying](deploying.md): a production database, and Alembic migrations
  instead of `async_create_all`
- [New & Existing Projects](projects.md): add Restly
  beside the routes you already have
- [API Reference](api_reference.md)
