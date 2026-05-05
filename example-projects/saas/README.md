# SaaS Example

Multi-tenant project management API built with [FastAPI-Restly](https://github.com/rutgerprins/fastapi-restly).

This is the most complete example in the repository. It is intended as a
showcase of how a real fastapi-restly project can be structured and
customized.

## What this example demonstrates

- **Multi-tenant data model.** `Organization` is the tenant; users, projects,
  tasks, and labels all scope to it through a shared `TenantBase` view.
- **Permission patterns.** Tenant isolation, row-level filtering (only see
  your own tasks), and field-level redaction (only HR sees `salary`).
- **Relationships.** One-to-many (Org -> Users / Projects, Project -> Tasks)
  and many-to-many through an explicit association model (`TaskLabel`).
- **Enum fields** for role, status, priority, and task type — including a
  `TaskPriority` int-enum stored as an integer column via a `TypeDecorator`.
- **Custom create/update schemas** with field validation
  (`OrganizationCreateSchema`, `OrganizationUpdateSchema`).
- **Custom endpoints** alongside auto-generated CRUD: clone/archive/restore
  projects, soft delete via `@fr.delete`, `/me` self-service routes.
- **Query modifiers.** Field-name filters (`name=...`), operator suffixes
  (`created_at__gte=...`), sorting, and pagination are covered in
  `tests/test_query.py`.

The package layout under `app/` (models / schemas / views split into
sub-packages) is the recommended layout for projects beyond a single file.

## Project layout

```
saas/
├── app/
│   ├── main.py            # FastAPI app, fr.configure(), lifespan
│   ├── models/            # SQLAlchemy models
│   ├── schemas/           # Pydantic schemas
│   └── views/             # AsyncRestView subclasses (TenantBase + per-resource)
├── tests/                 # pytest suite (90 tests covering CRUD + query)
└── pyproject.toml
```

## Running the app

```bash
# From example-projects/saas/
uv sync
uv run uvicorn app.main:app --reload
```

The lifespan in `app/main.py` creates the SQLite tables on startup, so a
fresh checkout will work without any extra migration step. The DB file is
written to `saas.db` (gitignored).

Once the server is running, the interactive docs are at
<http://127.0.0.1:8000/docs>.

## Running the tests

```bash
# From example-projects/saas/
uv run pytest
```

Tests use an in-memory SQLite database with savepoint-based isolation, so
nothing persists between runs.

## Further reading

- [Main framework documentation](../../docs/index.md)
- [User guide](../../docs/user_guide.md)
- [Other example projects](..)
