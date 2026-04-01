# How-To Guides

Task-focused guides for common FastAPI-Restly workflows.

::::{grid} 1 2 2 2
:gutter: 3

:::{grid-item-card} Custom Schemas and Aliases
:link: howto_custom_schema
:link-type: doc

Define schemas with field aliases, write-only fields, and read-only computed fields.
:::

:::{grid-item-card} Override Endpoints
:link: howto_override_endpoints
:link-type: doc

Customize `process_*` hooks and add custom routes alongside generated CRUD.
:::

:::{grid-item-card} Filter, Sort, and Paginate
:link: howto_query_modifiers
:link-type: doc

Use V1 (JSONAPI-style) and V2 (HTTP-style) query parameter interfaces.
:::

:::{grid-item-card} Foreign Keys with IDSchema
:link: howto_relationship_idschema
:link-type: doc

Reference related objects by ID with automatic resolution and 404 on missing.
:::

:::{grid-item-card} Testing
:link: howto_testing
:link-type: doc

Use `RestlyTestClient` and savepoint-based pytest fixtures for isolated tests.
:::

:::{grid-item-card} pytest Fixtures Reference
:link: pytest_fixtures
:link-type: doc

Full fixture reference with isolation model details and async test setup.
:::

:::{grid-item-card} Existing Project Integration
:link: howto_existing_project
:link-type: doc

Plug FastAPI-Restly into a project that already manages its own sessions.
:::
::::

```{toctree}
:maxdepth: 1
:hidden:

howto_custom_schema
howto_override_endpoints
howto_query_modifiers
howto_relationship_idschema
howto_testing
pytest_fixtures
howto_existing_project
```
