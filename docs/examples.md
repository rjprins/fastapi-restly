# Examples

The repository includes complete applications under `example-projects/`. They
show different levels of Restly adoption, from a generated starter project to
a production-shaped service with shared view foundations and custom behavior.

## Starter

[example-projects/starter](https://github.com/rjprins/fastapi-restly/tree/main/example-projects/starter)
is exactly what [`restly new myapp`](#restly-new) writes with its default
answers: async views, PostgreSQL, and Alembic. Read it to see the prescribed
layout at its smallest; a check in CI keeps it identical to the generator's
output. It depends on the released framework rather than this checkout, so
local changes to Restly do not appear in it.

(example-blog)=
## Blog

[example-projects/blog](https://github.com/rjprins/fastapi-restly/tree/main/example-projects/blog)
([README](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/blog/README.md))
is the app that the tutorials build: posts and comments with authors, drafts
that only their author can see, and a publish action. Its `blog/part1.py` is
the complete file of [Build a Blog API](tutorial.md), and `blog/main.py` is
the complete file of [Add Authors and Drafts](tutorial_customizing.md). Its
tests check each rule of that tutorial. It uses async views and SQLite.

## Shop

[example-projects/shop](https://github.com/rjprins/fastapi-restly/tree/main/example-projects/shop)
([README](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/shop/README.md))
shows relationships, multiple primary-key styles, async sessions, and
React-Admin-compatible endpoints through {class}`AsyncReactAdminView <fastapi_restly.views.AsyncReactAdminView>`. It also includes
a small React Admin frontend that uses the API. These patterns are
covered in [React Admin](howto_react_admin.md) and
[Relationships](howto_relationship_idschema.md).

## SaaS

[example-projects/saas](https://github.com/rjprins/fastapi-restly/tree/main/example-projects/saas)
([README](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/README.md))
is the most complete example: a multi-tenant project management API with
permission patterns, shared base views, mixins, custom create/update schemas,
[query modifiers](howto_query_modifiers.md), and
[Alembic migrations](deploying.md#migrations-with-alembic). Its runtime uses
PostgreSQL through `asyncpg`, Pydantic settings, and an application-owned async
engine. Compose provides separate development and test databases, and the
Restly test fixtures build their schema from the checked-in migrations. It also
builds a substantial non-CRUD surface on the same views. Each view lives beside
its model and schemas in a subject package under `example-projects/saas/app/`,
the layout [New & Existing Projects](howto_project_structure.md) describes.
The routes below highlight the patterns involved:

| Route | Pattern it demonstrates |
|---|---|
| `POST /tasks/{id}/start` / `complete` / `reopen` | State transitions via {meth}`write_action <fastapi_restly.views.RestView.write_action>` on a {class}`RestView <fastapi_restly.views.RestView>` |
| `POST /tasks/bulk`, `/tasks/bulk-delete`, `/tasks/import-csv` | Per-row savepoints inside one `shared_write_action_commit()` block |
| `POST /projects/{id}/clone` | A project, its outbox event, and copied tasks committed together |
| `POST /uploads` + `GET /uploads/{id}/lines` | A file-upload flow with a custom create bracket |
| `POST /task-labels/create-and-attach` | Two rows committed through one `write_action` block |
| `POST /users/{id}/change-password`, `GET /users/me` | Account actions and a non-resource read |

These patterns are covered in
[Compose Views with Mixins](howto_compose_views_with_mixins.md),
[Share Behaviour with Base Views](howto_inheritance.md),
[Request Lifecycle & Hooks](customize.md), and
[Patterns](patterns.md).

## Running them

Each project is self-contained: run `uv sync` in its directory, then use the
commands in its README (linked above).

The SaaS example also needs PostgreSQL. Its README includes the complete
Compose, migration, application, and test commands.

To build one of these yourself, start with [Build a Blog API](tutorial.md).
