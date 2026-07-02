# About FastAPI-Restly

## What it is

FastAPI-Restly is a REST framework for resource-shaped APIs built with FastAPI
and SQLAlchemy 2. The goal is to keep repeated API patterns in one place while
staying close to the FastAPI and SQLAlchemy code you already write.

FastAPI deliberately handles one side of a web application: routing,
validation, serialization, and dependency injection. Restly focuses on the
resource layer around that: SQLAlchemy sessions, ORM-to-schema mapping,
generated CRUD routes, query parameters, error translation, and test fixtures.

## Philosophy

- **DRY, but standard.** Reuse should come from conventions on top of the
  stack you already know, not from a parallel abstraction that hides it. You
  can always drop one level down to plain FastAPI or SQLAlchemy.
- **Out of the box.** The common path should not need wiring: engine and
  session setup, commit handling, schema generation,
  [list filtering](howto_query_modifiers.md) with strict validation,
  [error translation](howto_error_responses.md), and
  [savepoint-isolated test fixtures](howto_testing.md)
  all work from {func}`fr.configure() <fastapi_restly.db.configure>` onward.
- **Customization is never off the path.** Applications are rarely just CRUD.
  Every generated operation has explicit override points
  so generated behavior is a starting point, not a boundary (see [three tiers](the_handle_design.md)).
- **Documentation you don't have to guess at.** Common paths have runnable
  examples and extension points say where custom behavior belongs, for your
  team.

The core patterns ([class-based views](class_based_views.md), the override
hierarchy, and schema generation) are proven by four years of internal
production use. The public
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
deployments (four years of internal use, plus the lessons of the earlier
Flask framework) are what became the public project. We thank all three
companies for the environments that made it possible.

## Privacy

This site uses [GoatCounter](https://www.goatcounter.com/) for analytics, an
open-source, **cookieless** tool. It counts page views and unique visits
without setting cookies or storing personal data, so nothing tracks you across
sites and there is nothing to consent to. No data is shared with third parties.

Ready to try it? [Getting Started](getting_started.md) takes about fifteen
minutes.
