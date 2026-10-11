# Blog Example

The blog API that the FastAPI-Restly tutorials build. `blog/part1.py` is the
complete file of
[Tutorial: Build a Blog API](https://www.fastapi-restly.org/tutorial.html):
posts and comments. `blog/main.py` continues it, and is the complete file of
[Tutorial: Add Authors and Drafts](https://www.fastapi-restly.org/tutorial_customizing.html).
The documentation shows both files directly. The framework's own test suite
runs `part1.py`; the tests here check `main.py`.

## What it demonstrates

- The current user, read from a demo `X-User-ID` header and bound with
  `Current.depends(...)`
- An author column that a column default fills from `Current`
- Drafts that only their author can see, through the model's `default_scope`
- `authorize`: anyone may read, and only the author may change a post or a
  comment
- A `POST /posts/{id}/publish` action through `write_action`
- `after_action_commit`, which tells subscribers about a new post
- A base view that posts and comments share

## Run the API server

```sh
cd example-projects/blog
uv sync
uv run fastapi dev blog/main.py
```

The server creates the SQLite database (`blog.db`) and its tables on startup,
in the FastAPI `lifespan` hook. Visit <http://127.0.0.1:8000/docs> for the
interactive OpenAPI UI. Send an `X-User-ID` header to act as a writer; it is
a demo stand-in for real authentication. The tutorials show `curl` requests
to start with.

## Run the tests

```sh
cd example-projects/blog
uv run pytest -v
```

The tests use a separate SQLite database in memory, and Restly's test fixtures roll
back each test's changes. Each test checks one rule from the tutorial.

## Further reading

- Main framework documentation: [`/docs/`](../../docs/)
- Top-level [README](../../README.md)
- For relationships and React Admin compatibility, see [`../shop/`](../shop/)
- For a production-shaped service with tenants and mixins, see
  [`../saas/`](../saas/)
