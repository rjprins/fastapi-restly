# Guide

The Guide has one page for each part of Restly. Each page starts with how
that part works, then shows how to use it. The questions below link to the
section that answers each one. If you are new to Restly, start with the
tutorial, [Build a Blog API](tutorial.md).

## Start

Pages: [New & Existing Projects](projects.md),
[Database & Configuration](database.md).

- [How do I add Restly to a FastAPI app I already have?](#add-to-existing-app)
- [How do I write one CRUD route by hand and keep the rest?](#hand-written-crud-route)
- [How do I start a new project with `restly new`?](#restly-new)
- [How should I lay out a growing project?](#project-layout)
- [Where do I register my views?](#compose-in-one-place)
- [How do I serve more than one version of my API?](#api-versioning)
- [How do I tell Restly where its database is?](#configure-restly)
- [How do I give Restly the engine or sessionmaker my app already has?](#reuse-existing-engine)
- [How do I use another database, such as a read replica, on one view?](#session-dependency-one-view)
- [How do I open a session in a script, a worker or a background task?](#session-off-request)

## Models

Page: [Models](models.md).

- [How do I add Restly's defaults to my own declarative base?](#model-defaults)
- [Can I use the SQLAlchemy models I already have?](#existing-models)
- [How do I give a model an id and timestamps?](#model-mixins)
- [How do I record who created or last changed a row?](#server-stamped-fields)
- [Where do I set a value that every write needs, such as a slug from the title?](#write-path-values)
- [How do I keep the rows of each tenant apart (multi-tenancy)?](#tenant-row-scoping)
- [How do I soft-delete rows?](#soft-delete-mixin)

## Routes

Pages: [Views](views.md), [CRUD Routes](crud_routes.md),
[Request Lifecycle & Hooks](lifecycle.md).

- [Should I use `View`, `AsyncRestView`, `RestView` or a plain FastAPI function?](#choose-a-view-class)
- [How do I declare a route, or serve several HTTP methods on one path?](#declare-a-route)
- [How do I add a view to my app or router?](#register-a-view)
- [How do I share behavior between several views?](#share-behaviour-with-base-views)
- [Where do login, refresh and logout routes go?](#login-routes)
- [How do I receive a webhook?](#receive-a-webhook)
- [Which routes does a CRUD view serve?](#default-crud-routes)
- [Should I use `AsyncRestView` or `RestView`?](#choose-async-or-sync)
- [How do I leave out some CRUD routes, or make a view read-only?](#exclude-crud-routes)
- [How do I serve a child resource under its parent, such as `/projects/{id}/tasks`?](#nested-resources)
- [How does a CRUD route handle a request, step by step?](#view-request-steps)
- [Which method do I override for my change?](#which-method-to-override)
- [How do I change the status code or headers of one CRUD route?](#replace-endpoint-method)
- [How do I add a custom read route, such as `GET /users/me`?](#custom-read-route)
- [How do I look a row up by a slug or an email?](#natural-key-route)
- [How do I add an action route, such as publish?](#custom-action-route)
- [How do I commit several writes together, as in a bulk route?](#shared-write-action-commit)

## Requests and responses

Pages: [Schemas & Validation](schemas.md),
[Responses](howto_response_schema.md),
[Relationships](howto_relationship_idschema.md),
[Filtering & Pagination](howto_query_modifiers.md),
[Errors](howto_error_responses.md).

- [Should Restly generate the schema, or should I write it?](#write-or-generate)
- [How do I hide a field from responses, or stop clients from setting it?](#readonly-and-writeonly)
- [How do I validate input, and where does each kind of rule go?](#validation)
- [How do I use other field names in the JSON, such as camelCase?](#field-aliases)
- [How do I send other fields than the client sends?](#own-response-class)
- [What does each CRUD route return by default?](#default-responses)
- [How do I change the list envelope?](#change-list-envelope)
- [How do I change the response body of one route, such as an envelope around one object?](#one-route-shape)
- [How do I change the shape of every response?](#to-response)
- [How do I change how one object is serialized?](#to-single-response)
- [How do I use a smaller schema for the list route?](#list-schema)
- [How does a custom route return the same types as the CRUD routes?](#name-the-response-classes)
- [Should my API send ids or whole objects for related rows?](#reference-styles)
- [How do I make sure that a foreign key points to a row that exists?](#reference-exists)
- [Which rows can a reference reach?](#visibility-and-multi-tenancy)
- [How do I return related objects in a response?](#relationship-loading)
- [How do I fix `MissingGreenlet`?](#missinggreenlet)
- [Which filters can a client use on a list?](#filter-a-list)
- [How do I filter a list on a field of a related row?](#relation-filtering)
- [How do clients page through a list, and how do I set the page limits?](#list-pagination)
- [How do I return every row without pagination?](#no-pagination)
- [How do I return a 404, 403 or 409 from my own code?](#raise-http-error)
- [How does a duplicate unique value become a 409?](#default-exception-handling)
- [How do I change the error body for the whole app?](#error-envelope)

## Rules

Pages: [Current & ContextParam](howto_current.md), [Query Clauses](clauses.md),
[Scopes](scopes.md), [Authorization](authorization.md).

- [How do I bind the current user from my authentication dependency?](#current-request-binding)
- [How do I bind the current user in a script or a job?](#current-manual-binding)
- [How do I write a reusable query rule?](#declaring-clauses)
- [How do I hide rows, such as deleted rows, from every read?](#default-scope)
- [How do I show each user only their own rows?](#read-scope)
- [How does one route read past the scope, such as a restore action?](#per-read-scope)
- [I know scopes from Rails, Laravel or Phoenix. What are they in Restly?](#coming-from-rails)
- [How do I decide who may read, create, update, delete or run an action?](#authorize-gate)
- [Why does a hidden row give a 404, and a refused read a 403?](#read-request-steps)
- [Where does a value go that only the request sets, such as a hashed password?](#business-methods)
- [What commits, and when?](#what-commits)
- [How do I run code before or after the commit, such as an outbox row or an email?](#transaction-hooks)
- [How do I run the same logic in a script, a worker or an import?](#logic-outside-a-view)

## Operate

Pages: [Testing](howto_testing.md), [Deploying](deploying.md),
[OpenAPI](howto_openapi.md), [React Admin](howto_react_admin.md),
[Type Annotations](howto_typing.md).

- [How do I set up a test suite for my app?](#test-setup)
- [How do I build the test schema, from models or from migrations?](#test-databases-and-migrations)
- [How do I use another user in a test?](#current-testing)
- [Which pytest fixtures does Restly provide?](#pytest-fixture-reference)
- [How do I read the database settings from the environment?](#production-settings)
- [How do I run the app in production?](#running-the-app)
- [How do I set up migrations with Alembic?](#alembic)
- [How do I add a health check endpoint?](#health-checks)
- [Which engine options does Restly set for me?](#engine-defaults)
- [How do I change the name, summary or operation ID of a CRUD route?](#route-options)
- [How do I document a custom route in OpenAPI?](#document-custom-route)
- [How do I connect react-admin to a Restly view?](#react-admin-quick-start)
- [How do I type the view methods that I override?](#view-generics)

## What FastAPI covers

Restly runs inside a FastAPI app, and
{func}`include_view <fastapi_restly.views.include_view>` registers plain HTTP
routes. So these subjects are FastAPI's, and its docs explain them:

- [Security](https://fastapi.tiangolo.com/tutorial/security/): OAuth2, API
  keys and other security schemes. To make the caller available to your
  views, see [Current & ContextParam](#current-request-binding).
- [Middleware](https://fastapi.tiangolo.com/tutorial/middleware/).
- [CORS](https://fastapi.tiangolo.com/tutorial/cors/). For a react-admin
  frontend, see also [CORS for React Admin](#react-admin-cors).
- [Background tasks](https://fastapi.tiangolo.com/tutorial/background-tasks/).
  For work that must still happen after a crash, use an outbox row in a
  [transaction hook](#after-action-commit-raises).
- [File uploads](https://fastapi.tiangolo.com/tutorial/request-files/).
- [WebSockets](https://fastapi.tiangolo.com/advanced/websockets/). Declare
  them on the app or on a router, not on a view.

```{toctree}
:maxdepth: 1
:hidden:

projects
database
models
views
crud_routes
lifecycle
schemas
howto_response_schema
howto_relationship_idschema
howto_query_modifiers
howto_error_responses
howto_current
clauses
scopes
authorization
howto_testing
deploying
howto_openapi
howto_react_admin
howto_typing
```
