# FastAPI-Restly

[![CI](https://github.com/rjprins/fastapi-restly/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/rjprins/fastapi-restly/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue)](https://github.com/rjprins/fastapi-restly/blob/main/pyproject.toml)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](https://github.com/rjprins/fastapi-restly/blob/main/LICENSE)
[![Coverage](https://rjprins.github.io/fastapi-restly/coverage/badge.svg)](https://rjprins.github.io/fastapi-restly/coverage/)

:::{image} _static/restly-cat.png
:width: 320px
:align: center
:::

FastAPI-Restly (`fr`) is a framework that supplements FastAPI with instant CRUD endpoints, built on SQLAlchemy 2.0 and Pydantic v2.

FastAPI-Restly implements **true class-based views** — real Python classes that support inheritance and method overrides. Share common behavior across views by subclassing, and override individual CRUD methods without touching the rest. See [Class-Based Views](class_based_views.md) for why this is the heart of the framework.

> **Status:** `0.5.0` — first public beta release.
>
> After four years of internal development at two separate companies, Restly is finally ready for its first public release! Right now the goal is to see if the public API of Restly hits the right abstractions, and to stabilize the API for a `1.0.0` release. From `0.5.0` onwards, expect small breaking changes in naming and functionality on the deeper parts of the API surface. Feedback is always appreciated!

## Quick Start

The maintained copy-paste Quick Start lives in [Getting Started](getting_started.md). It covers database setup, dev table creation, async vs sync views, auto-generated schemas, explicit schemas, and the generated endpoint surface.

Use auto-schema when you want speed and low boilerplate. Use explicit schemas when you need strict public API contracts, custom validation, aliases, or field-level serialization control.

## Features

- **Instant CRUD endpoints** — GET, POST, PATCH, DELETE with zero boilerplate
- **True class-based views** — Real inheritance and method overrides; share logic across views by subclassing
- **React Admin ready** — `AsyncReactAdminView` speaks `ra-data-simple-rest` out of the box; no custom data provider needed
- **SQLAlchemy 2.0 support** — Async-first with modern patterns
- **Pydantic v2 integration** — Full validation and serialization
- **Automatic schema generation** — Read, create, and update schemas generated automatically
- **List parameters** — Filter, sort, and paginate from a stable URL dialect generated from the response schema
- **Relationship support** — Handle foreign keys and nested objects
- **Testing utilities** — Built-in test helpers with savepoint isolation

## Documentation

::::{grid} 1 2 2 3
:gutter: 3

:::{grid-item-card} Getting Started
:link: getting_started
:link-type: doc

Fast path from zero to a working CRUD API.
:::

:::{grid-item-card} Class-Based Views
:link: class_based_views
:link-type: doc

The heart of the framework — how subclassable views unlock everything else.
:::

:::{grid-item-card} User Guide
:link: user_guide
:link-type: doc

Tutorial walkthroughs and in-depth topic guides covering every framework feature.
:::

:::{grid-item-card} API Reference
:link: api_reference
:link-type: doc

Generated endpoints, all public symbols, query parameters, and autodoc.
:::

:::{grid-item-card} About
:link: about
:link-type: doc

History, design goals, and why this framework exists.
:::
::::

## Installation

```bash
pip install "fastapi-restly[standard]"
```

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
user_guide
deploying
api_reference
about
```
