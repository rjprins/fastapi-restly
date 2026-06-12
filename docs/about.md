# About FastAPI-Restly

## What it is

FastAPI-Restly is a REST framework on top of FastAPI, SQLAlchemy 2, and
Pydantic v2 — and a step toward something larger: a more complete web
framework.

FastAPI deliberately handles one side of a web application: routing,
validation, serialization, dependency injection. Everything on the other
side — session and transaction handling, the standard resource endpoints,
list filtering, error responses, test setup — every project builds again,
slightly differently, the same patterns rewritten over and over.

Restly exists to make that side DRY without making it exotic. It stays
standard — plain FastAPI and SQLAlchemy underneath, nothing hidden — and
provides general out-of-the-box functionality on top: define a model and a
schema, get a working API, and override one method when your service needs
to differ.

## Philosophy

- **DRY, but standard.** Reuse should come from conventions on top of the
  stack you already know, not from a parallel abstraction that hides it. You
  can always drop one level down to plain FastAPI or SQLAlchemy.
- **Out of the box.** The common path should not need wiring: engine and
  session setup, commit handling, schema generation, list filtering with
  strict validation, error translation, and savepoint-isolated test fixtures
  all work from `fr.configure()` onward.
- **Customization is never off the path.** Real production services are
  never simply CRUD. Every generated operation has explicit override points
  — the [three tiers](the_handle_design.md) — so generated behavior is a
  starting point, not a boundary.
- **A holistic view of web applications.** The long-term goal covers more
  than resource endpoints: auth, permissions, background jobs, admin pages,
  and a plugin system are on the path, each with the same escape hatches.
- **Documentation you don't have to guess at.** Common paths have runnable
  examples and extension points say where custom behavior belongs — for your
  team and for coding agents alike.

The core patterns — class-based views, the override hierarchy, schema
generation — are proven by four years of internal production use. The public
API surface is still settling on the way to `1.0.0` (see the
[changelog](changelog.md)); a broader user base will find corners we haven't
hit yet. If you find something broken or missing,
[open an issue](https://github.com/rjprins/fastapi-restly/issues).

## History

Restly is built and maintained by [Rutger Prins](https://github.com/rjprins),
and the pattern behind it predates FastAPI. At **EclecticIQ**, an earlier
private framework on Flask and Flask-Classy explored the same idea: define a
model, define a schema, get a working API, override individual methods where
needed. It was never released publicly.

When FastAPI and SQLAlchemy 2 matured, the approach was rebuilt as Restly at
**Clearblue Markets**, which later allowed the work to be shared publicly,
and then developed and refined in production at **Brenntag**. Those
deployments — four years of internal use, plus the lessons of the earlier
Flask framework — are what became the public project. Thanks to all three
companies for the environments that made it possible.

Ready to try it? [Getting Started](getting_started.md) takes about fifteen
minutes.
