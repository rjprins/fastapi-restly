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
React-Admin-compatible endpoints through `AsyncReactAdminView`. It also includes
a small React Admin frontend wired against the API. Covered in:
[React Admin Integration](howto_react_admin.md),
[Work with Foreign Keys Using IDRef](howto_relationship_idschema.md).

## SaaS

[example-projects/saas](https://github.com/rjprins/fastapi-restly/tree/main/example-projects/saas)
([README](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/README.md))
is the most complete example: a multi-tenant project management API with
permission patterns, shared base views, mixins, custom create/update schemas,
query modifiers, Alembic migrations — and a substantial **non-CRUD surface**
built on the same views. Route highlights, all from
`example-projects/saas/app/views/`:

| Route | Pattern it demonstrates |
|---|---|
| `POST /tasks/{id}/start` / `complete` / `reopen` | State transitions via `write_action` on a `RestView` |
| `POST /tasks/bulk`, `/tasks/bulk-delete`, `/tasks/import-csv` | Bulk endpoints beside generated CRUD |
| `POST /uploads/` + `GET /uploads/{id}/lines` | A file-upload flow with a custom create bracket |
| `POST /task-labels/create-and-attach` | Two rows committed through one `write_action` block |
| `POST /users/{id}/change-password`, `GET /users/me` | Account actions and a non-resource read |

Covered in:
[Compose Views with Mixins](howto_compose_views_with_mixins.md),
[Share Behaviour with Base Views](howto_inheritance.md),
[Override CRUD Behavior](howto_override_endpoints.md),
[Patterns](patterns.md).

## Running them

Each project is self-contained: run `uv sync` in its directory, then use the
commands in its README (linked above).

Want to build one of these yourself? Start with
[Getting Started](getting_started.md) and the [Tutorial](tutorial.md).
