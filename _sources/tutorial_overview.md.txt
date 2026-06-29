# Tutorial

This tutorial builds a small blog API with two related models, walking through the
most common FastAPI-Restly patterns. It assumes you have read
[Getting Started](getting_started.md) and installed `fastapi-restly[standard]`
with the `aiosqlite` driver.

It comes in two parts:

- **[Part 1: Generated CRUD](tutorial.md)** — define the models and schemas, then
  get full CRUD endpoints from a single view class.
- **[Part 2: Customizing Views](tutorial_customizing.md)** — override handlers, add
  custom routes, and share behaviour with base classes.

```{toctree}
:maxdepth: 1
:hidden:

tutorial
tutorial_customizing
```
