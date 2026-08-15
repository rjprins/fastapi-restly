# myapp

A REST API built with [FastAPI-Restly](https://www.fastapi-restly.org).

## Getting started

```bash
uv sync
docker compose up -d --wait db test-db
cp .env.example .env
uv run alembic revision --autogenerate -m "initial"
uv run alembic upgrade head
uv run fastapi dev
```

The API is then at <http://127.0.0.1:8000>, with interactive documentation at
<http://127.0.0.1:8000/docs> and a liveness endpoint at
<http://127.0.0.1:8000/health>.

## Layout

```text
myapp/
├── main.py        Application factory and the VIEWS it registers
├── asgi.py        app = create_app(), the only module a server imports
├── settings.py    Environment settings
└── tasks/         One resource: model, schemas, view
tests/
```

Each resource is a package holding its model, its schemas, and its view,
because those three change together. Add a package beside `tasks/`, then add
its view to `VIEWS` in `main.py`.

Importing `main.py` must stay free of side effects: it defines `create_app()`
and builds nothing, which is what lets the test suite name its own database.

## Services

`compose.yaml` runs two PostgreSQL databases: `db` for development and `test-db`
for the test suite, so a test run never touches development data.

## Migrations

Alembic owns the schema. After changing a model:

```bash
uv run alembic revision --autogenerate -m "describe the change"
uv run alembic upgrade head
```

`alembic/env.py` imports `myapp.main`, which reaches every view and
through each view its models, so autogenerate sees the whole schema. A model no
view reaches must be imported wherever it is used. Run `alembic check` in CI to
catch one that is missed.

## Tests

```bash
uv run pytest
```

The suite builds its schema by running the migrations, so it needs at
least one before it passes. Its database is separate from the
development one.

## Further reading

- [Structure a project](https://www.fastapi-restly.org/howto_project_structure.html)
- [Deploying](https://www.fastapi-restly.org/deploying.html)
- [Testing](https://www.fastapi-restly.org/howto_testing.html)
