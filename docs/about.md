# About FastAPI-Restly

## What it is

FastAPI-Restly is a CRUD framework for FastAPI and SQLAlchemy 2. It generates
standard REST endpoints from a model and schema definition, and gets out of the
way when you need to go beyond the standard case.

The central idea is that the five CRUD operations — list, get, create, update,
delete — follow the same structure in almost every API. Repeating that structure
by hand doesn't add value. FastAPI-Restly handles it, so the code you write is
the code that makes your application different.

## History

The pattern behind FastAPI-Restly is older than FastAPI itself.

Around 2016, I built the first version at **EclecticIQ**, on top of Flask and
Flask-Classy. The same idea: define a model, define a schema, get a working API
— and override individual methods when you need to. That version went into
production and stayed there.

When FastAPI and SQLAlchemy 2 matured, the approach translated naturally. I
rebuilt it from scratch at **Clearblue Markets**, who later allowed me to
open-source it. That was version 1. A second version was developed and refined
in production at **Brenntag**. That was version 2.

Version 3.0.0 is the first public release. The version number reflects the
history — two prior production versions, across three companies, over nearly ten
years of use.

## Honest about where we are

Production history at three companies is meaningful, but they were focused
deployments. A broader user base will find corners we haven't hit yet.

The core patterns — class-based views, the hook hierarchy, schema generation —
are proven. The public API surface is new. If you find something broken or
missing, [open an issue](https://github.com/rjprins/fastapi-restly/issues).

## Acknowledgements

Thanks to Clearblue Markets and Brenntag for allowing this work to be developed
and eventually shared. Thanks to EclecticIQ for the original environment where
this pattern was first put to the test.
