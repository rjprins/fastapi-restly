# How-To Guides

These guides are task-focused: each one shows how to accomplish a specific
goal with FastAPI-Restly. If you are new to the framework, start with
[Getting Started](getting_started.md), then the [Tutorial](tutorial.md), and
come back here when you have a concrete task.

::::{grid} 1 2 2 2
:gutter: 3

:::{grid-item-card} Use Restly in an Existing Project
:link: howto_existing_project
:link-type: doc

Adopt Restly beside existing FastAPI routes, then wire it into your sessions and models.
:::

:::{grid-item-card} Patterns
:link: patterns
:link-type: doc

Find the idiomatic answers for nested resources and sub-resources, a
different list schema, restoring soft-deleted rows, webhook receivers, login
flows, custom actions, and tenant scoping.
:::

:::{grid-item-card} Custom Schemas and Field Types
:link: howto_custom_schema
:link-type: doc

Define schemas with field aliases, write-only fields, and read-only computed fields.
:::

:::{grid-item-card} Response Envelopes and List Metadata
:link: howto_response_schema
:link-type: doc

Wrap successful responses in `{data}` envelopes and add list metadata such as
total counts, while keeping Restly's handlers and serialization.
:::

:::{grid-item-card} Work with Foreign Keys and Relationships
:link: howto_relationship_idschema
:link-type: doc

Check foreign-key columns with MustExist and resolve relationships with IDRef or IDSchema, with a 404 on missing rows.
:::

:::{grid-item-card} Filter, Sort, and Paginate Lists
:link: howto_query_modifiers
:link-type: doc

Filter, sort, and paginate list endpoints using URL query parameters.
:::

:::{grid-item-card} Override CRUD Behavior and Add Custom Endpoints
:link: howto_override_endpoints
:link-type: doc

Override the business methods ({meth}`create <fastapi_restly.views.RestView.create>`, {meth}`update <fastapi_restly.views.RestView.update>`, ...), the request handlers,
or the route shells, and add custom routes alongside generated CRUD.
:::

:::{grid-item-card} Shape Error Responses
:link: howto_error_responses
:link-type: doc

Learn the typed `fr.exc` exceptions to raise from overrides, the 422-vs-400
split, and app-wide error envelopes (problem+json).
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

Layer cross-cutting concerns (tenant scoping, soft delete, audit stamping)
through cooperative mixins.
:::

:::{grid-item-card} Use Type Annotations
:link: howto_typing
:link-type: doc

Use {class}`IDSchema <fastapi_restly.schemas.IDSchema>`, optional view generics, and typed CRUD methods without
fighting the framework.
:::

:::{grid-item-card} React Admin Integration
:link: howto_react_admin
:link-type: doc

Use {class}`AsyncReactAdminView <fastapi_restly.views.AsyncReactAdminView>` to get a backend that `ra-data-simple-rest` connects to out of the box.
:::

:::{grid-item-card} Customize the OpenAPI Schema
:link: howto_openapi
:link-type: doc

Set per-view tags and responses, add metadata to custom routes, replace a
generated route's documented contract, and use `x-resource-ref`.
:::

:::{grid-item-card} Test APIs with RestlyTestClient and Fixtures
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

howto_existing_project
patterns
howto_custom_schema
howto_response_schema
howto_relationship_idschema
howto_query_modifiers
howto_override_endpoints
howto_error_responses
howto_inheritance
howto_compose_views_with_mixins
howto_typing
howto_react_admin
howto_openapi
howto_testing
deploying
```
