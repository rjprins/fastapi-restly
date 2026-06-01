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

Before Restly existed, I built a similar private framework at **EclecticIQ**, on
top of Flask and Flask-Classy. It was never released as Restly. It explored the
same core idea: define a model, define a schema, get a working API, and override
individual methods when you need to.

When FastAPI and SQLAlchemy 2 matured, the approach translated naturally.
Restly was rebuilt for FastAPI at **Clearblue Markets**, who later allowed the
work to be shared publicly. It was later developed and refined in production at
**Brenntag**.

That is the production history behind the public project: focused production
use at two companies, plus lessons from the earlier private Flask work.

## Direction

The long-term goal is to provide as much out of the box as possible while still
leaving clear escape hatches. Restly should take a holistic view of building web
applications, not only the resource endpoints themselves.

That includes auth, permissions, background jobs, admin pages, a future plugin
system, and other customization layers that serve real-world use cases.

The documentation should be exhaustive and friendly to agentic coding. You, your
team, or a coding agent should not have to guess how to do a common thing.
Common paths should have examples, and extension points should explain where
custom behavior belongs.

## Honest about where we are

Production history at two companies is meaningful, but those were focused
deployments. A broader user base will find corners we haven't hit yet.

The core patterns — class-based views, the override hierarchy, and schema
generation — are proven. The public API surface is still settling. If you find
something broken or missing, [open an issue](https://github.com/rjprins/fastapi-restly/issues).

## Acknowledgements

Thanks to Clearblue Markets and Brenntag for allowing this work to be developed
and eventually shared. Thanks to EclecticIQ for the original environment where
the earlier Flask pattern was explored.
