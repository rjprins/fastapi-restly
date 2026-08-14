# SaaS Example

Multi-tenant project management API built with
[FastAPI-Restly](https://github.com/rjprins/fastapi-restly).

This is the repository's canonical production-shaped example. It uses the same
PostgreSQL, async engine, migration, schema, and test boundaries that a deployed
service needs.

## What this example demonstrates

- **Separate PostgreSQL databases.** Compose runs a persistent development
  database on port 5432 and an ephemeral test database on port 5433.
- **Validated settings.** `app/settings.py` requires `DATABASE_URL` and loads
  pool sizes with Pydantic settings, including explicit asyncpg and range
  validation.
- **Application factory.** `app/main.py` exposes `create_app(settings)`.
  Settings reading, engine construction, and Restly configuration run only
  when the factory is called. The test suite calls the same factory with its
  own database.
- **Application-owned engine.** `app/database.py` builds the async engine from
  settings, Restly receives that engine, and the FastAPI lifespan disposes its
  pool.
- **Migration-owned schema.** The app never calls `async_create_all()`.
  `alembic/` contains an async environment and the initial schema migration.
- **Migration-backed fixtures.** Restly runs `alembic upgrade head` against the
  test database before the suite. The migration seeds the read-only country
  lookup used by the tests.
- **Subject-first organization.** Each resource package keeps its SQLAlchemy
  model, Pydantic schemas, and Restly views together. Application-wide concerns
  remain in specifically named top-level modules.
- **Explicit API schemas.** Each subject's `schemas.py` defines its public
  Pydantic contracts, including operation-specific validation and read-only
  fields.
- **Canonical endpoint spelling.** Collection routes use no trailing slash in
  OpenAPI, templates, and contract tests, for example `POST /tasks`.
- **Real application patterns.** Tenant isolation, permissions, relationships,
  custom actions, query modifiers, multipart uploads, optimistic locking,
  soft deletion, and transactional outbox writes are all covered.

## Project layout

```text
saas/
├── .env.example             # Development settings template
├── compose.yaml             # Development and test PostgreSQL services
├── alembic.ini
├── alembic/
│   ├── env.py               # Async Alembic environment
│   └── versions/            # Initial schema and lookup seed data
├── app/
│   ├── database.py          # Application-owned async engine builder
│   ├── settings.py          # Validated Pydantic settings
│   ├── main.py              # create_app() factory: Restly wiring, lifespan
│   ├── api.py               # Central, side-effect-free view registration
│   ├── model_registry.py    # Explicit model discovery for Alembic
│   ├── view_base.py         # Application-wide Restly view foundation
│   ├── view_mixins.py       # Application-wide view behavior
│   ├── outbox.py            # Cross-domain transactional outbox model
│   ├── organizations/
│   │   ├── models.py
│   │   ├── schemas.py
│   │   └── views.py
│   ├── users/               # models.py, schemas.py, views.py
│   ├── projects/            # models.py, schemas.py, views.py
│   ├── tasks/               # models.py, schemas.py, views.py
│   ├── labels/              # models.py, schemas.py, views.py
│   ├── uploads/             # models.py, schemas.py, views.py
│   └── countries/           # models.py, schemas.py, views.py
├── tests/                   # Migration-backed Restly test suite
└── pyproject.toml
```

Domain modules only define classes and functions. `app/api.py` is the explicit
composition boundary that imports every view and registers it on the app. This
keeps imports directional: model, then schema, then view, then application.
`app/model_registry.py` provides the corresponding explicit model-import
boundary for SQLAlchemy and Alembic.

## Prerequisites

- [uv](https://docs.astral.sh/uv/)
- Docker with Compose

## Run the application

From `example-projects/saas/`:

```bash
uv sync
cp .env.example .env
docker compose up -d --wait db
uv run alembic upgrade head
uv run uvicorn --factory app.main:create_app --reload
```

The API is available at <http://127.0.0.1:8000> and the interactive OpenAPI
documentation is at <http://127.0.0.1:8000/docs>.

The application startup does not create tables. Run `alembic upgrade head`
after starting a fresh database and whenever a deployment includes new
migrations.

## Work with migrations

Run these commands from `example-projects/saas/` with the development database
running:

```bash
# Show the applied revision
uv run alembic current

# Apply all migrations
uv run alembic upgrade head

# Generate a migration after changing models, then review the generated file
uv run alembic revision --autogenerate -m "describe the change"

# Revert one revision
uv run alembic downgrade -1

# Verify that models and the migrated schema still agree
uv run alembic check
```

The environment under `alembic/` was created with:

```bash
alembic init -t async alembic
```

The async template is required because `DATABASE_URL` uses
`postgresql+asyncpg`. Alembic's generic synchronous template cannot open that
URL.

## Run the tests

The test suite never connects to the development database:

```bash
docker compose up -d --wait test-db
uv run pytest
```

`tests/conftest.py` builds the application under test with
`create_app(Settings(database_url=...))`, selecting `saas_test` on port 5433,
then calls `fr.testing.configure_tests(..., alembic_upgrade=True)`. No
environment variable has to change before an import. Restly applies the same
migrations used in deployment and wraps each test in rollback isolation. Rows
seeded by migrations remain available to every test.

To use another disposable PostgreSQL database:

```bash
SAAS_TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5544/saas_test uv run pytest
```

From the repository root, this command starts a throwaway PostgreSQL container
when `RESTLY_TEST_DATABASE_URL` is not already set:

```bash
make test-saas
```

## Stop the services

```bash
docker compose down
```

The development data remains in the named `saas-data` volume. The test service
uses an in-memory filesystem, so its data disappears with the container.

## Endpoint spelling

Use collection paths without a trailing slash in new code, for example
`GET /tasks` and `POST /tasks`. Restly still accepts `/tasks/` as a hidden
compatibility alias, but OpenAPI records only `/tasks`.

## Further reading

- [Deployment guide](../../docs/deploying.md)
- [Testing guide](../../docs/howto_testing.md)
- [Examples guide](../../docs/examples.md)
- [Main framework documentation](../../docs/index.md)
- [Alembic asyncio recipe](https://alembic.sqlalchemy.org/en/latest/cookbook.html#using-asyncio-with-alembic)
- [Pydantic settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)
- [SQLAlchemy asyncio](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)
- [FastAPI lifespan events](https://fastapi.tiangolo.com/advanced/events/)
