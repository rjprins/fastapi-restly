# Using RestView

{class}`AsyncRestView <fastapi_restly.views.AsyncRestView>` and
{class}`RestView <fastapi_restly.views.RestView>` define five CRUD endpoint
methods for one SQLAlchemy model. A subclass supplies the URL prefix, model,
and optional Pydantic schemas. {func}`include_view()
<fastapi_restly.views.include_view>` registers the inherited endpoint methods
as FastAPI routes:

```python
@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
```

`UserView` now serves list, create, retrieve, update, and delete requests under
`/users`. [Getting Started](getting_started.md) supplies the surrounding app,
database configuration, and model in one runnable file.

## Choose async or sync

Use {class}`AsyncRestView <fastapi_restly.views.AsyncRestView>` for new
applications unless the application already uses synchronous SQLAlchemy. It
receives an `AsyncSession` through `fr.AsyncSessionDep`, and methods you
override are normally `async def` methods.

Use {class}`RestView <fastapi_restly.views.RestView>` with synchronous
SQLAlchemy sessions or sync-only libraries. It receives a `Session` through
`fr.SessionDep`. Both classes expose the same routes, configuration, and
override points. Mixed applications can choose per view.

## Define the resource

Every CRUD view needs two class attributes:

- {attr}`prefix <fastapi_restly.views.View.prefix>` is the URL prefix shared by
  every route in the view.
- {attr}`model <fastapi_restly.views.BaseRestView.model>` is the SQLAlchemy
  mapped class that the routes read and write.

Ordinary models based on SQLAlchemy's `DeclarativeBase` work. Restly's
{class}`IDBase <fastapi_restly.models.IDBase>` is an optional convenience base,
not a requirement.

If the view does not declare {attr}`schema
<fastapi_restly.views.BaseRestView.schema>`, Restly constructs a response schema
from `model` when the view is registered. It then derives the create and update
schemas from that response schema. Declare an explicit response schema when the
wire contract needs stable field names, validation, aliases, or fields that do
not match the table directly:

```python
class UserRead(fr.IDSchema):
    name: str
    email: str


@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserRead
```

The three schema attributes have separate jobs:

| Attribute | Used for | Default |
|---|---|---|
| {attr}`schema <fastapi_restly.views.BaseRestView.schema>` | `GET` responses and successful write responses | Constructed from `model` when omitted |
| {attr}`schema_create <fastapi_restly.views.BaseRestView.schema_create>` | `POST` request body | `schema` without `ReadOnly` fields |
| {attr}`schema_update <fastapi_restly.views.BaseRestView.schema_update>` | `PATCH` request body | Writable fields from `schema`, made optional |

[Custom Schemas and Field Types](howto_custom_schema.md) owns field markers,
aliases, validation, and the choice between explicit and constructed schemas.

For larger applications, define the class without registration side effects
and include it where the app or router is assembled:

```python
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserRead


fr.include_view(app, UserView)
```

Both registration forms produce the same routes.
[Project structure](howto_project_structure.md) shows where the direct call belongs in a
multi-package application.

(default-crud-behavior)=
## Default CRUD behavior

For `prefix = "/users"`, `include_view` registers the inherited endpoint
methods as this HTTP contract:

| Request | Endpoint method | Input | Response | Status |
|---|---|---|---|---|
| `GET /users` | {meth}`get_many_endpoint <fastapi_restly.views.AsyncRestView.get_many_endpoint>` | List query parameters | Paginated `data` envelope | `200` |
| `POST /users` | {meth}`create_endpoint <fastapi_restly.views.AsyncRestView.create_endpoint>` | `schema_create` | `schema` | `201` |
| `GET /users/{id}` | {meth}`get_one_endpoint <fastapi_restly.views.AsyncRestView.get_one_endpoint>` | Scalar path ID | `schema` | `200` |
| `PATCH /users/{id}` | {meth}`update_endpoint <fastapi_restly.views.AsyncRestView.update_endpoint>` | `schema_update` | `schema` | `200` |
| `DELETE /users/{id}` | {meth}`delete_endpoint <fastapi_restly.views.AsyncRestView.delete_endpoint>` | Scalar path ID | Empty body | `204` |

The collection path without a trailing slash is canonical. Restly also
accepts the trailing-slash form as a hidden compatibility route.

List filters are derived from fields shared by `schema` and `model`. Sorting
uses `?sort=field,-other_field`. Pagination is on by default with a page size
of 50, a maximum accepted page size of 1000, and a response shaped like:

```json
{
  "data": [{"id": 1, "name": "Jane", "email": "jane@example.com"}],
  "total_count": 1,
  "page": 1,
  "page_size": 50,
  "total_pages": 1
}
```

Unknown list query keys return `422` instead of being ignored. [Filter, Sort,
and Paginate Lists](howto_query_modifiers.md) defines the complete query
grammar. [Response Envelopes and List Metadata](howto_response_schema.md)
defines the default containers and how to replace them.

Missing or hidden rows return `404`. On the default endpoints, the create,
update, and delete handlers run authorization, call the business method, run
the commit hooks, and commit before the response is built. A custom endpoint
can group several handlers under one
{meth}`shared_write_action_commit() <fastapi_restly.views.AsyncRestView.shared_write_action_commit>`
block. The outermost block then owns the commit and after-hooks. Reads do not
commit.

:::{important}
Restly does not authenticate requests or impose an authorization policy.
{meth}`authorize() <fastapi_restly.views.AsyncRestView.authorize>` permits every
action unless a subclass overrides it. Add authentication through FastAPI
dependencies, then enforce per-action policy in `authorize()` where needed.
:::

## Configure a view

Configure the class according to the contract the resource needs:

| Requirement | Configuration |
|---|---|
| Stable response fields | Declare {attr}`schema <fastapi_restly.views.BaseRestView.schema>` |
| Different create or update validation | Declare {attr}`schema_create <fastapi_restly.views.BaseRestView.schema_create>` or {attr}`schema_update <fastapi_restly.views.BaseRestView.schema_update>` |
| UUID or another scalar ID type | Set {attr}`id_type <fastapi_restly.views.BaseRestView.id_type>` |
| Read-only or otherwise restricted routes | Set {attr}`exclude_routes <fastapi_restly.views.BaseRestView.exclude_routes>` with `fr.ViewRoute` values |
| Disable pagination | Set {attr}`paginated <fastapi_restly.views.BaseRestView.paginated>` to `False` |
| Change page limits | Set {attr}`default_page_size <fastapi_restly.views.BaseRestView.default_page_size>` and {attr}`max_page_size <fastapi_restly.views.BaseRestView.max_page_size>` |
| Accept a view-specific list query key | Add it to {attr}`extra_query_params <fastapi_restly.views.BaseRestView.extra_query_params>` |
| Apply FastAPI metadata or dependencies to every route | Set {attr}`tags <fastapi_restly.views.View.tags>`, {attr}`responses <fastapi_restly.views.View.responses>`, or {attr}`dependencies <fastapi_restly.views.View.dependencies>` |
| Use another session dependency | Override the `session` annotation with `Annotated[..., Depends(...)]` |

[API Reference](api_reference.md) lists the exact attribute types and method
signatures. [Use Restly in an Existing Project](howto_existing_project.md)
covers custom engines, session generators, and a per-view session dependency.

## Change behavior

A request passes through an endpoint method, a handler, and a business method.
Change the layer that owns the behavior instead of rewriting the entire route:

| Change | Use |
|---|---|
| Stamp or transform data during create or update | Override the business method in [Customizing RestView](customize.md); a server-stamped field is a column default on the model |
| Hide rows from every read | Declare a [scope](scopes.md): `default_scope` on the model, or `scope` on the view |
| Permit or reject an action | Override {meth}`authorize() <fastapi_restly.views.AsyncRestView.authorize>` |
| Run an atomic side effect or a post-commit action | Override `before_action_commit()` or `after_action_commit()` |
| Commit several handlers or custom actions together | Wrap them in {ref}`shared_write_action_commit() <shared-write-action-commit>` |
| Change status, headers, request parameters, or response model | Replace the endpoint method |
| Add a path that is not part of CRUD | Add a method with `@fr.get`, `@fr.post`, or another route decorator |
| Share behavior across resources | Use a [base view](howto_inheritance.md) or [mixins](howto_compose_views_with_mixins.md) |
| Replace the list query grammar | Follow [Filter, Sort, and Paginate Lists](howto_query_modifiers.md) |

[Customizing RestView](customize.md) owns the lifecycle diagrams, complete
override decision table, and worked recipes.

## Limits and alternatives

The default CRUD contract has these boundaries:

- Resource identity is one scalar primary key on the generated routes. Set
  `id_type` for UUID or another scalar type. A composite key is reached from
  a custom route that loads with a predicate
  ([Look a row up by another key](#natural-key-route)), or from
  {class}`View <fastapi_restly.views.View>` with explicit route paths.
- Nested response schemas and relationship filtering are supported. General
  nested create and update payloads are not. Use `MustExist`, `IDRef`, or
  `IDSchema` for model-aware references, or transform the payload in a business
  method. See [Work with Foreign Keys and
  Relationships](howto_relationship_idschema.md).
- The default update route uses `PATCH`, not `PUT`. Add an explicit `PUT` route
  only when the client contract requires one.
- A {class}`View <fastapi_restly.views.View>` groups non-CRUD endpoints without
  adding CRUD methods. A plain FastAPI route remains the clearest choice for a
  single endpoint that shares no view configuration.

## Next steps

- [Getting Started](getting_started.md) builds and runs a first resource.
- [Customizing RestView](customize.md) changes behavior at the correct layer.
- [Custom Schemas and Field Types](howto_custom_schema.md) defines stable wire
  contracts.
- [Views](class_based_views.md) explains registration, shared
  dependencies, and the inheritance model.
- [How-To Guides](user_guide.md) covers individual tasks.
- [API Reference](api_reference.md) lists exact signatures and defaults.
