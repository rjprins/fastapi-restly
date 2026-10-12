# Tutorial: Build a Blog API

In this tutorial we build the API for a blog: posts, and comments on posts.
At the end you have a running API with ten routes, documented in the browser,
with filtering and paging. You also know what Restly adds to FastAPI. The
tutorial takes about twenty minutes. [Add Authors and
Drafts](tutorial_customizing.md) continues from the file you build here.

## Install

Python 3.10 or later is required. Install Restly with FastAPI's development
server, and the async SQLite driver:

```bash
pip install "fastapi-restly[standard]" aiosqlite
```

## Create and list posts

A blog needs posts. Create `main.py`. The first block is setup: it opens the
database, declares the base class for the models, and creates the tables when
the app starts. You do not change this block again in this tutorial.

```{literalinclude} ../example-projects/blog/blog/part1.py
:language: python
:end-at: app = FastAPI(lifespan=lifespan)
```

Below it, add a model and a view:

```{literalinclude} ../example-projects/blog/blog/part1.py
:language: python
:start-at: class Post(Base):
:end-at: model = Post
```

`Post` is a normal SQLAlchemy model. `PostView` is the Restly part:
{attr}`prefix <fastapi_restly.views.View.prefix>` is the URL, `model` is the
table, and {func}`fr.include_view <fastapi_restly.views.include_view>` adds
the view's routes to the app. Start the server:

```bash
fastapi dev main.py
```

Open <http://127.0.0.1:8000/docs>. The posts resource has five routes:

| Method   | Path          | Action          |
|----------|---------------|-----------------|
| `GET`    | `/posts`      | List posts      |
| `POST`   | `/posts`      | Create a post   |
| `GET`    | `/posts/{id}` | Get one post    |
| `PATCH`  | `/posts/{id}` | Update one post |
| `DELETE` | `/posts/{id}` | Delete one post |

Each route shows its request and response schema. Restly generated them from
the model: `PostCreate` for the body of `POST`, `PostResponse` for a post in
a response, and so on. Create a post with the **Try it out** button, or with
`curl` from a second terminal:

```bash
curl -X POST http://127.0.0.1:8000/posts \
  -H 'Content-Type: application/json' \
  -d '{"title": "Hello", "content": "My first post"}'
```

The response is `201 Created` with the stored post:

```json
{"id": 1, "title": "Hello", "content": "My first post", "published": false}
```

`published` was not in the request, so the column default applied. The list
route returns the post in a page:

```bash
curl http://127.0.0.1:8000/posts
```

```json
{
  "data": [{"id": 1, "title": "Hello", "content": "My first post", "published": false}],
  "total_count": 1,
  "page": 1,
  "page_size": 50,
  "total_pages": 1
}
```

This is what Restly adds to FastAPI: a model and a four-line view class give
a complete, documented resource. [CRUD Routes](rest_views.md) describes
the full contract of these routes.

## Comment on posts

Readers want to comment on a post. A comment belongs to one post, so the
model has a foreign key. Add the model and its view at the end of the file:

```python
class Comment(Base):
    __tablename__ = "comment"

    id: Mapped[int] = mapped_column(primary_key=True)
    content: Mapped[str]
    post_id: Mapped[int] = mapped_column(ForeignKey("post.id"))


@fr.include_view(app)
class CommentView(fr.AsyncRestView):
    prefix = "/comments"
    model = Comment
```

The server restarts by itself when you save the file, and the comments
resource gets the same five routes. Now comment on a post that does not
exist:

```bash
curl -X POST http://127.0.0.1:8000/comments \
  -H 'Content-Type: application/json' \
  -d '{"content": "Nice post", "post_id": 999}'
```

```json
{"detail": "Foreign key constraint violated"}
```

The status is `409 Conflict`. The database refused the row, and Restly turned
the database error into an HTTP error. That is correct, but it comes after
the insert was tried, and it does not say which field was wrong. A missing
post should answer `404`, and name the field. So comments need to check the
post before the insert.

Until now, Restly generated the view's schema from the model. For comments we
write the schema ourselves, and mark `post_id` as a checked reference. Put
the schema above `CommentView`, and point the view at it:

```{literalinclude} ../example-projects/blog/blog/part1.py
:language: python
:start-at: class CommentSchema(fr.IDSchema):
```

A {attr}`schema <fastapi_restly.views.BaseRestView.schema>` says which fields
the API accepts and returns. {class}`fr.IDSchema <fastapi_restly.schemas.IDSchema>`
adds the `id` field as read-only, so you do not declare it.
{class}`fr.MustExist[int, Post] <fastapi_restly.schemas.MustExist>` is a
plain integer in JSON, and Restly checks that a `Post` with that id exists
before it writes the comment. Send the same request again:

```json
{"detail": "Id not found for post_id: 999"}
```

The status is `404 Not Found`, and the message names the field. A comment on
post `1` is accepted:

```bash
curl -X POST http://127.0.0.1:8000/comments \
  -H 'Content-Type: application/json' \
  -d '{"content": "Nice post", "post_id": 1}'
```

```json
{"id": 1, "content": "Nice post", "post_id": 1}
```

[Relationships](howto_relationship_idschema.md)
covers references in more detail, including nested objects in responses.

## Find posts

Readers want to find posts. The list route already filters, sorts and pages
through URL parameters, so this step adds no code. Create a second post, and
mark it as published:

```bash
curl -X POST http://127.0.0.1:8000/posts \
  -H 'Content-Type: application/json' \
  -d '{"title": "Second", "content": "Now published", "published": true}'
```

Then try these:

```bash
curl 'http://127.0.0.1:8000/posts?published=true'
# only the second post, with "total_count": 1

curl 'http://127.0.0.1:8000/posts?title__icontains=hello'
# only the first post

curl 'http://127.0.0.1:8000/posts?sort=-id&page=1&page_size=10'
# both posts, newest first
```

A field name filters on equality. A suffix such as `__icontains` chooses
another operator. `sort` takes field names, with `-` for descending order.
[Filtering & Pagination](howto_query_modifiers.md) lists every
operator and the paging settings.

## The complete file

Here is `main.py` with everything from this tutorial. The same file is the
first stage of the [blog example](https://github.com/rjprins/fastapi-restly/tree/main/example-projects/blog), and the test suite runs it.

```{literalinclude} ../example-projects/blog/blog/part1.py
:language: python
```

## Next steps

[Add Authors and Drafts](tutorial_customizing.md) continues with this file.
It adds the current user, drafts that only their author can see, a publish
action, and rules for who may change a post.

These pages go deeper into what this tutorial used:

- [CRUD Routes](rest_views.md): the default CRUD contract and the view
  settings
- [How Restly generates a schema](#auto-generated-schemas): what
  Restly generates from a model, and when to write a schema
- [Relationships](howto_relationship_idschema.md):
  checked references and nested objects
- [Filtering & Pagination](howto_query_modifiers.md): every filter
  operator, sorting, and paging
- [Testing](howto_testing.md): test the API, with a rollback after each test
- [Deploying](deploying.md): Alembic migrations instead of `async_create_all`
- [Views](views.md): views without a model, and
  [the sync `RestView`](#choose-async-or-sync)

```{toctree}
:maxdepth: 1
:hidden:

tutorial_customizing
```
