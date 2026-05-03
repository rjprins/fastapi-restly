# Blog Example

The smallest possible FastAPI-Restly example: a single `Blog` model with one
field, exposed as a full CRUD REST resource via sync `RestView`. Use this as a
starting point or smoke test.

## What it demonstrates

- Auto-generated CRUD endpoints (`GET`, `POST`, `PATCH`, `DELETE`) from a
  SQLAlchemy 2.0 dataclass-style model
- Connecting to a sync SQLite database via `fr.configure(...)`
- Creating tables on startup via the FastAPI `lifespan` hook
- Auto-derived request/response Pydantic schemas when the view omits
  `schema = ...`
- Sync request sessions through `SessionDep` and standalone sessions through
  `fr.open_session()`

## Run the API server

```sh
cd example-projects/blog
uv sync
uv run uvicorn blog.main:app --reload --port 8000
```

The server creates the SQLite database (`blog.db`) and tables on startup via
the FastAPI `lifespan` hook. Visit <http://127.0.0.1:8000/docs> for the
interactive OpenAPI UI.

## Run the tests

```sh
cd example-projects/blog
uv run pytest -v
```

Tests run against an in-memory SQLite database (see `tests/conftest.py`); they
do not touch `blog.db`.

## Further reading

- Main framework documentation: [`/docs/`](../../docs/)
- Top-level [README](../../README.md)
- For a richer example with relationships and React Admin compatibility, see
  [`../shop/`](../shop/)
- For a customization-heavy showcase, see [`../saas/`](../saas/)
