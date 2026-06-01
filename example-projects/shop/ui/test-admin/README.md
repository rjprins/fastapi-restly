# test-admin

A React Admin frontend for the FastAPI-Restly `shop` example. Demonstrates
that `AsyncReactAdminView` is wire-compatible with
[`ra-data-simple-rest`](https://github.com/marmelab/react-admin/tree/master/packages/ra-data-simple-rest)
out of the box -- no custom data provider needed.

## Prerequisites

The shop API server must be running. From the repo root:

```sh
cd example-projects/shop
uv sync
uv run uvicorn shop.main:app --reload --port 8001
```

## Installation

```sh
npm install
```

## Development

```sh
npm run dev
```

This starts Vite on http://localhost:5173 and proxies API calls to whatever
URL is configured in `.env`.

## Production build

```sh
npm run build
```

## Type checking

```sh
npm run type-check
```

Runs `tsc -p tsconfig.app.json --noEmit` against the `src/` tree.

## End-to-end tests

A Playwright suite lives under `e2e/`. With both the API server and the Vite
dev server running:

```sh
npx playwright test
```

## Configuration

The data provider URL is read from the `VITE_API_URL` environment variable in
`.env` (defaulting to `http://localhost:8001` if unset). Update `.env` to
point the UI at a different backend.

## Why this works without a custom data provider

`ra-data-simple-rest` expects:

- List endpoints to return a plain JSON array
- A `Content-Range` response header for pagination
- Query params `?range=[0,9]&sort=["name","ASC"]&filter={...}`

`AsyncReactAdminView` (and its sync counterpart) matches that contract. See
`tests/test_react_admin.py` in the shop project for a compact wire spec.
