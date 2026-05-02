# Shop Example

A minimal FastAPI-Restly example showcasing auto-generated CRUD endpoints
that are wire-compatible with [React Admin](https://marmelab.com/react-admin/)
via `AsyncReactAdminView`.

## What it demonstrates

- Auto-generated CRUD endpoints from SQLAlchemy 2.0 dataclass-style models
- Three primary key strategies: integer (`IDBase`), UUID, and integer with
  timestamps (`IDBase + TimestampsMixin`)
- A many-to-many relationship (`Product` <-> `Order`) via an association table
- A one-to-many relationship (`Customer` -> `Order`)
- React-Admin-compatible list endpoints (range/sort/filter query params and
  `Content-Range` response header) with no custom data provider needed
- A small React Admin frontend wired against the API (under `ui/test-admin/`)

The framework auto-derives the request/response Pydantic schemas from the
`fr.IDSchema` definitions; no manual schema duplication is required.

## Run the API server

```sh
cd example-projects/shop
uv sync
uv run uvicorn shop.main:app --reload --port 8001
```

The server creates the SQLite database (`shop.db`) and tables on startup via
the FastAPI `lifespan` hook.

## Run the tests

```sh
cd example-projects/shop
uv run pytest -v
```

Tests run against an in-memory SQLite database with savepoint-based isolation
(see `tests/conftest.py`); they do not touch `shop.db`.

## React Admin frontend

A working React Admin UI lives under `ui/test-admin/`. With the API server
running on port 8001:

```sh
cd ui/test-admin
npm install
npm run dev
```

See [`ui/test-admin/README.md`](ui/test-admin/README.md) for details, including
the Playwright e2e suite.

## Further reading

- Main framework documentation: [`/docs/`](../../docs/)
- React Admin how-to: [`/docs/howto_react_admin.md`](../../docs/howto_react_admin.md)
- Top-level [README](../../README.md)
