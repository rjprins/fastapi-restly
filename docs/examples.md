# Examples

The repository includes complete applications under `example-projects/`. They
show different levels of Restly adoption, from a tiny resource to a
production-shaped service with shared view foundations and custom behavior.

## Blog

[example-projects/blog](https://github.com/rjprins/fastapi-restly/tree/main/example-projects/blog)
([README](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/blog/README.md))
is the smallest example: one model, one view, sync SQLAlchemy sessions, and
auto-generated schemas. Use it as a smoke test or as the shortest path from an
empty app to a working REST resource.

## Shop

[example-projects/shop](https://github.com/rjprins/fastapi-restly/tree/main/example-projects/shop)
([README](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/shop/README.md))
shows relationships, multiple primary-key styles, async sessions, and
React-Admin-compatible endpoints through {class}`AsyncReactAdminView <fastapi_restly.views.AsyncReactAdminView>`. It also includes
a small React Admin frontend wired against the API. These patterns are
covered in [React Admin Integration](howto_react_admin.md) and
[Work with Foreign Keys and Relationships](howto_relationship_idschema.md).

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
the layout [Structure a Project](howto_project_structure.md) describes.
The routes below highlight the patterns involved:

| Route | Pattern it demonstrates |
|---|---|
| `POST /tasks/{id}/start` / `complete` / `reopen` | State transitions via {meth}`write_action <fastapi_restly.views.RestView.write_action>` on a {class}`RestView <fastapi_restly.views.RestView>` |
| `POST /tasks/bulk`, `/tasks/bulk-delete`, `/tasks/import-csv` | Bulk endpoints beside generated CRUD |
| `POST /uploads` + `GET /uploads/{id}/lines` | A file-upload flow with a custom create bracket |
| `POST /task-labels/create-and-attach` | Two rows committed through one `write_action` block |
| `POST /users/{id}/change-password`, `GET /users/me` | Account actions and a non-resource read |

These patterns are covered in
[Compose Views with Mixins](howto_compose_views_with_mixins.md),
[Share Behaviour with Base Views](howto_inheritance.md),
[Customize RestView](customize.md), and
[Patterns](patterns.md).

## Running them

Each project is self-contained: run `uv sync` in its directory, then use the
commands in its README (linked above).

The SaaS example also needs PostgreSQL. Its README includes the complete
Compose, migration, application, and test commands.

To build one of these yourself, start with
[Getting Started](getting_started.md) and the [Tutorial](tutorial.md).
