# Guide

These guides are task-focused: each one shows how to accomplish a specific
goal with FastAPI-Restly. If you are new to the framework, start with the
tutorial, [Build a Blog API](tutorial.md). Come back here when you have a
concrete task.

## Adopting and Modeling

These guides cover bringing Restly into a codebase and defining the models and
schemas it works from.

::::{grid} 1 2 2 2
:gutter: 3

:::{grid-item-card} Use Restly in an Existing Project
:link: howto_existing_project
:link-type: doc

Adopt Restly beside existing FastAPI routes, then connect it to your sessions and models.
:::

:::{grid-item-card} New & Existing Projects
:link: howto_project_structure
:link-type: doc

Lay out a growing application by subject, keep imports pointing one way, and
compose views and models from one place each.
:::

:::{grid-item-card} Models
:link: howto_models
:link-type: doc

Give your own declarative base Restly's defaults, change one default, and add `id` and timestamp columns.
:::

:::{grid-item-card} Schemas & Validation
:link: howto_custom_schema
:link-type: doc

Define schemas with field aliases, write-only fields, and read-only computed fields.
:::

:::{grid-item-card} Relationships
:link: howto_relationship_idschema
:link-type: doc

Check foreign-key columns with MustExist and resolve relationships with IDRef or IDSchema, with a 404 on missing rows.
:::

::::

## Shaping Requests and Responses

These guides control what list endpoints accept and what your API returns,
including errors.

::::{grid} 1 2 2 2
:gutter: 3

:::{grid-item-card} Responses
:link: howto_response_schema
:link-type: doc

Wrap successful responses in `{data}` envelopes and add list metadata such as
total counts, while keeping Restly's handlers and serialization.
:::

:::{grid-item-card} Relationship Loading and Async
:link: howto_relationship_loading
:link-type: doc

Load nested relationships for responses on reads and writes, extend the loaded
set, and resolve `MissingGreenlet` on async sessions.
:::

:::{grid-item-card} Filtering & Pagination
:link: howto_query_modifiers
:link-type: doc

Filter, sort, and paginate list endpoints using URL query parameters.
:::

:::{grid-item-card} Current & ContextParam
:link: howto_current
:link-type: doc

Bind the current user per request. Read the value in application code,
column defaults, and tests.
:::

:::{grid-item-card} Query Clauses
:link: clauses
:link-type: doc

Compose reusable SQLAlchemy predicates. Supply values through context
members when applying the clauses to a statement.
:::

:::{grid-item-card} Scopes
:link: scopes
:link-type: doc

Impose a replaceable clause on every read and every reference to a model.
Use a default scope for row surfaces such as live and deleted records.
:::

:::{grid-item-card} Errors
:link: howto_error_responses
:link-type: doc

Learn the typed `fr.exc` exceptions to raise from overrides, the 422-vs-400
split, and app-wide error envelopes (problem+json).
:::

::::

## Customizing Views

These guides change what CRUD views do, from overriding a single
method to sharing behaviour across many views.

::::{grid} 1 2 2 2
:gutter: 3

:::{grid-item-card} Views
:link: class_based_views
:link-type: doc

Choose a view type, then learn how registration, dependencies, and inheritance
work.
:::

:::{grid-item-card} CRUD Routes
:link: rest_views
:link-type: doc

Define a model-backed CRUD resource and understand its default contract.
:::

:::{grid-item-card} Patterns
:link: patterns
:link-type: doc

Find the idiomatic answers for nested resources and sub-resources, a
different list schema, restoring soft-deleted rows, webhook receivers, login
flows, custom actions, and tenant scoping.
:::

:::{grid-item-card} Nested Resources
:link: howto_nested
:link-type: doc

Serve a child resource under its parent, such as
`/projects/{project_id}/tasks`, with paging, filters, sort and every CRUD
route.
:::

:::{grid-item-card} Request Lifecycle & Hooks
:link: customize
:link-type: doc

Override the business methods ({meth}`create <fastapi_restly.views.RestView.create>`, {meth}`update <fastapi_restly.views.RestView.update>`, ...), the handlers,
or the endpoint methods, and add custom routes alongside the default CRUD
routes.
:::

:::{grid-item-card} Share Behaviour with Base Views
:link: howto_inheritance
:link-type: doc

Use Python inheritance to share CRUD overrides, dependencies, URL prefixes, and
access control across multiple views.
:::

:::{grid-item-card} Compose Views with Mixins
:link: howto_compose_views_with_mixins
:link-type: doc

Put cross-cutting concerns (tenant scoping, soft delete, audit stamping)
where they belong: structural fields on the model, verbs on view mixins.
:::

:::{grid-item-card} Type Annotations
:link: howto_typing
:link-type: doc

Use {class}`IDRef <fastapi_restly.schemas.IDRef>`, optional view generics, and
typed CRUD methods that your type checker understands.
:::

::::

## Operating and Testing

These guides connect the API to the outside world: frontends, OpenAPI
consumers, tests, and production deployment.

::::{grid} 1 2 2 2
:gutter: 3

:::{grid-item-card} React Admin
:link: howto_react_admin
:link-type: doc

Use {class}`AsyncReactAdminView <fastapi_restly.views.AsyncReactAdminView>` to get a backend that `ra-data-simple-rest` connects to out of the box.
:::

:::{grid-item-card} OpenAPI
:link: howto_openapi
:link-type: doc

Set per-view tags and responses, add metadata to custom routes, replace a
default CRUD route's documented contract, and use `x-resource-ref`.
:::

:::{grid-item-card} Testing
:link: howto_testing
:link-type: doc

Start from a copy-paste conftest, work with savepoint-isolated fixtures, and
consult the full fixture reference.
:::

:::{grid-item-card} Deploying
:link: deploying
:link-type: doc

Configure a production engine, set up Alembic migrations, and follow an ASGI
checklist.
:::

::::

```{toctree}
:maxdepth: 1
:hidden:

howto_project_structure
howto_existing_project
howto_models
howto_compose_views_with_mixins
class_based_views
howto_inheritance
rest_views
howto_nested
customize
howto_custom_schema
howto_response_schema
patterns
howto_relationship_idschema
howto_relationship_loading
howto_query_modifiers
howto_error_responses
howto_current
clauses
scopes
howto_testing
deploying
howto_openapi
howto_react_admin
howto_typing
```
