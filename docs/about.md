# About FastAPI-Restly

## What it is

FastAPI-Restly is a REST framework for FastAPI and SQLAlchemy 2. It generates
standard resource endpoints from a model and schema definition, and gets out of
the way when your service needs custom behavior.

The central idea is code reuse: keep FastAPI projects DRY without hiding the
framework underneath. Most services repeat the same endpoint structure, session
wiring, schema conversion, list filtering, and response handling. Repeating that
by hand doesn't add value. FastAPI-Restly handles the common path, so the code
you write is the code that makes your application different.

Customization must always be possible. Restly is intended for real production
web services, where things are never simply CRUD. Generated operations should
be a strong starting point, not a boundary.

## History

The pattern behind FastAPI-Restly is older than FastAPI itself.

Restly is built and maintained by [Rutger Prins](https://github.com/rjprins).
Before Restly existed, I built a similar private framework at **EclecticIQ**,
on top of Flask and Flask-Classy; it was never released publicly. It explored
the same core idea: define a model, define a schema, get a working API, and
override individual methods when you need to.

When FastAPI and SQLAlchemy 2 matured, the approach translated naturally.
Restly was rebuilt for FastAPI at **Clearblue Markets**, who later allowed the
work to be shared publicly. It was then developed and refined in production at
**Brenntag**.

That is the four years of internal use behind the public project: focused
production deployments of Restly itself at two companies — Clearblue Markets
and Brenntag — plus the lessons from the earlier private Flask framework at
EclecticIQ.

## Direction

The long-term goal is to provide as much out of the box as possible while still
leaving clear escape hatches. Restly should take a holistic view of building web
applications, not only the resource endpoints themselves.

That includes auth, permissions, background jobs, admin pages, a future plugin
system, and other customization layers that serve real-world use cases. The
documentation aims to keep pace: common paths get examples, extension points
say where custom behavior belongs, and no reader — human or coding agent —
should have to guess.

## How Restly compares

The closest neighbor is [FastCRUD](https://github.com/benavlabs/fastcrud),
which generates CRUD endpoints through an endpoint factory (`crud_router`)
backed by a `FastCRUD` service class of async CRUD methods. It is good at what
it targets — fast joins, offset and cursor pagination, minimal setup. The
architectural difference is what happens when an endpoint needs to deviate:
with an endpoint factory you bypass it and hand-write the route against the
service class; with Restly's class-based views you subclass and override one
tier of the existing route (the business verb, the request handler, or the
route shell) while the framework keeps owning routing, authorization hooks,
and the commit. Restly also takes positions FastCRUD leaves to you: a
request-scoped session and commit bracket, `authorize` / `before_commit` /
`after_commit` hooks, schema-derived list filtering with strict 422 validation,
and savepoint-isolated test fixtures. If your service is CRUD plus joins and
you prefer wiring the rest yourself, FastCRUD is a fine choice; if your
endpoints accumulate domain behavior over time, that is the case Restly is
built for. (FastAPI's own
[Alternatives page](https://fastapi.tiangolo.com/alternatives/) is the genre
model for this kind of comparison.)

## Honest about where we are

Production history at two companies is meaningful, but those were focused
deployments. A broader user base will find corners we haven't hit yet.

The core patterns — class-based views, the override hierarchy, and schema
generation — are proven. The public API surface is still settling (see the
[changelog](changelog.md)). If you find something broken or missing,
[open an issue](https://github.com/rjprins/fastapi-restly/issues).

## Acknowledgements

Thanks to Clearblue Markets and Brenntag for allowing this work to be developed
and eventually shared. Thanks to EclecticIQ for the original environment where
the earlier Flask pattern was explored.

Ready to try it? [Getting Started](getting_started.md) takes about fifteen
minutes.
