# User Guide

In-depth coverage of every FastAPI-Restly feature. Start with the tutorial if you
are new to the framework; jump to any topic guide if you are looking for something
specific.

---

## Tutorial

A walkthrough that builds a complete blog API from scratch, introducing the core
patterns progressively.

::::{grid} 1 2 2 2
:gutter: 3

:::{grid-item-card} Part 1: Building a REST API
:link: tutorial
:link-type: doc

Models, schemas, generated endpoints, read/write field control, querying lists,
and testing.
:::

:::{grid-item-card} Part 2: Customizing Views
:link: tutorial_customizing
:link-type: doc

Override handlers, low-level object helpers, custom routes, and shared base classes.
:::

::::

---

## Topic Guides

::::{grid} 1 2 2 2
:gutter: 3

:::{grid-item-card} Auth, Actions, and Other Non-CRUD Endpoints
:link: class_based_views
:link-type: doc

Login/logout flows, vote and state-transition actions, webhook receivers, and
RPC-style endpoints: use `fr.View` directly, or add custom routes to a
`RestView`. See "When to use `View` directly".
:::

:::{grid-item-card} Custom Schemas and Aliases
:link: howto_custom_schema
:link-type: doc

Define schemas with field aliases, write-only fields, and read-only computed fields.
:::

:::{grid-item-card} Type Annotations
:link: howto_typing
:link-type: doc

Use `IDSchema`, optional view generics, and typed CRUD methods (`get_many`, `create`, `update`, ...) without fighting the framework.
:::

:::{grid-item-card} Override Endpoints
:link: howto_override_endpoints
:link-type: doc

Override the business methods (`create`, `update`, ...), the request handlers, or the route shells, and add custom routes alongside generated CRUD.
:::

:::{grid-item-card} React Admin Integration
:link: howto_react_admin
:link-type: doc

Use `AsyncReactAdminView` to get a backend that `ra-data-simple-rest` connects to out of the box.
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

Layer cross-cutting concerns — tenant scoping, soft delete, audit stamping —
through cooperative mixins. Includes the rule for when to layer in a mixin (e.g.
by overriding `make_new_object` / `update_object` cooperatively) vs. write logic
in the `create` / `update` business methods.
:::

:::{grid-item-card} Filter, Sort, and Paginate
:link: howto_query_modifiers
:link-type: doc

Filter, sort, and paginate list endpoints using URL query parameters.
:::

:::{grid-item-card} Foreign Keys with IDRef
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

Adopt Restly beside existing FastAPI routes, then wire it into your sessions and models.
:::

:::{grid-item-card} Technical Details
:link: technical_details
:link-type: doc

Schema generation internals, view registration, and list-parameters lifecycle.
:::

::::

```{toctree}
:maxdepth: 1
:hidden:

tutorial
tutorial_customizing
howto_custom_schema
howto_typing
howto_override_endpoints
howto_react_admin
howto_inheritance
howto_compose_views_with_mixins
howto_query_modifiers
howto_relationship_idschema
howto_testing
pytest_fixtures
howto_existing_project
technical_details
```
