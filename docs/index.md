# FastAPI-Restly

[![CI](https://github.com/rjprins/fastapi-restly/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/rjprins/fastapi-restly/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue)](https://github.com/rjprins/fastapi-restly/blob/main/pyproject.toml)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](https://github.com/rjprins/fastapi-restly/blob/main/LICENSE)
[![Coverage](https://rjprins.github.io/fastapi-restly/coverage/badge.svg)](https://rjprins.github.io/fastapi-restly/coverage/)

:::{image} _static/restly-cat.png
:width: 320px
:align: center
:::

FastAPI-Restly (`fr`) is a REST framework for FastAPI, backed by SQLAlchemy 2.0 and Pydantic v2.

Views are real Python classes. Share behavior with inheritance and mixins;
override the one operation you need. See [Class-Based Views](class_based_views.md).

> **Status:** `0.6.1` — public beta release.
>
> Restly is public after four years of internal use. The API is settling on the
> way to `1.0.0`; expect small breaking changes in deeper extension points.
> Feedback is welcome.

## Quick Start

The maintained copy-paste Quick Start lives in [Getting Started](getting_started.md).
It covers setup, dev tables, async vs sync views, schemas, and generated endpoints.

Use auto-schema for speed. Use explicit schemas for public contracts, validation, aliases, and serialization control.

## Features

- **[Generated REST endpoints](api_reference.md#generated-rest-endpoints)** — GET, POST, PATCH, DELETE with minimal boilerplate
- **[True class-based views](class_based_views.md)** — inheritance, mixins, and method overrides
- **[React Admin ready](howto_react_admin.md)** — `AsyncReactAdminView` speaks `ra-data-simple-rest`
- **[SQLAlchemy 2.0 support](getting_started.md)** — async-first with modern patterns
- **[Pydantic v2 integration](howto_custom_schema.md)** — validation and serialization for public contracts
- **[Automatic schema generation](technical_details.md#auto-generated-schemas)** — read, create, and update schemas generated automatically
- **[List parameters](howto_query_modifiers.md)** — filter, sort, and paginate from a stable URL dialect generated from the response schema
- **[Relationship support](howto_relationship_idschema.md)** — handle foreign keys and nested objects
- **[Testing utilities](howto_testing.md)** — built-in test helpers with savepoint isolation

## Documentation

::::{grid} 1 2 2 3
:gutter: 3

:::{grid-item-card} Getting Started
:link: getting_started
:link-type: doc

Fast path from zero to a working REST API.
:::

:::{grid-item-card} Class-Based Views
:link: class_based_views
:link-type: doc

How subclassable views make the override model work.
:::

:::{grid-item-card} User Guide
:link: user_guide
:link-type: doc

Tutorials and topic guides.
:::

:::{grid-item-card} API Reference
:link: api_reference
:link-type: doc

Generated endpoints, all public symbols, query parameters, and autodoc.
:::

:::{grid-item-card} Examples
:link: examples
:link-type: doc

Complete sample applications from a tiny API to a production-shaped service.
:::

:::{grid-item-card} About
:link: about
:link-type: doc

History, design goals, and why this framework exists.
:::
::::

## Installation

```bash
pip install "fastapi-restly[standard]" aiosqlite
```

The `standard` extra mirrors `fastapi[standard]` (the `fastapi dev` server
toolchain). Restly is database-driver-agnostic, so install the async driver for
your database alongside it — `aiosqlite` for SQLite (used in the examples),
`asyncpg`/`psycopg` for PostgreSQL. Test tooling lives in a separate extra:
`pip install "fastapi-restly[testing]"`.

## Development

```bash
git clone https://github.com/rjprins/fastapi-restly.git
cd fastapi-restly
uv sync
uv run pytest
```

```{toctree}
:maxdepth: 2
:hidden:

getting_started
class_based_views
the_handle_design
user_guide
deploying
examples
api_reference
about
```
