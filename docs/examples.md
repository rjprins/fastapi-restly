# Examples

The repository includes complete applications under `example-projects/`. They
show different levels of Restly adoption, from a tiny resource to a
production-shaped service with shared view foundations and custom behavior.

## Blog

[example-projects/blog](https://github.com/rjprins/fastapi-restly/tree/main/example-projects/blog)
is the smallest example: one model, one view, sync SQLAlchemy sessions, and
auto-generated schemas. Use it as a smoke test or as the shortest path from an
empty app to a working REST resource.

## Shop

[example-projects/shop](https://github.com/rjprins/fastapi-restly/tree/main/example-projects/shop)
shows relationships, multiple primary-key styles, async sessions, and
React-Admin-compatible endpoints through `AsyncReactAdminView`. It also includes
a small React Admin frontend wired against the API.

## SaaS

[example-projects/saas](https://github.com/rjprins/fastapi-restly/tree/main/example-projects/saas)
is the most complete example. It demonstrates a multi-tenant project management
API with permission patterns, shared base views, mixins, custom create/update
schemas, query modifiers, and custom endpoints alongside generated resource
operations.

Run each project from its own directory with `uv sync`, then use the commands in
that example's README.
