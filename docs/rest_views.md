# CRUD Routes

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
`/users`. [Build a Blog API](tutorial.md) shows the surrounding app, database
configuration, and model in one runnable file.

## Choose async or sync

Use {class}`AsyncRestView <fastapi_restly.views.AsyncRestView>` for new
applications unless the application already uses synchronous SQLAlchemy. It
receives an `AsyncSession` through `fr.AsyncSessionDep`, and methods you
override are normally `async def` methods.

Use {class}`RestView <fastapi_restly.views.RestView>` with synchronous
SQLAlchemy sessions or sync-only libraries. It receives a `Session` through
`fr.SessionDep`. Both classes expose the same routes, configuration, and
override points. Mixed applications can choose per view.

Both {class}`AsyncRestView <fastapi_restly.views.AsyncRestView>` (async) and {class}`RestView <fastapi_restly.views.RestView>` (sync) are public API and
share the same CRUD structure via an internal abstract base class (not exposed
as `fr.*`). The choice between them is determined by which class you subclass:
`AsyncRestView` declares `session: AsyncSessionDep` and `RestView` declares
`session: SessionDep`. A subclass can override that `session` annotation with
its own `Annotated[..., Depends(...)]` dependency to
[use another session on one view](#session-dependency-one-view).
The async and sync variants have identical endpoint signatures; the only
difference is that the async variant uses `await` in its process methods.

## Define the resource

Every CRUD view needs two class attributes:

- {attr}`prefix <fastapi_restly.views.View.prefix>` is the URL prefix shared by
  every route in the view.
- {attr}`model <fastapi_restly.views.BaseRestView.model>` is the SQLAlchemy
  mapped class that the routes read and write.

Ordinary models based on SQLAlchemy's `DeclarativeBase` work. Restly's
model defaults, {class}`fr.ModelDefaults <fastapi_restly.models.ModelDefaults>`,
are optional; see [Models](howto_models.md).

If the view does not declare {attr}`schema
<fastapi_restly.views.BaseRestView.schema>`, Restly generates the view's schema
from `model` when the view is registered. It then derives the create and update
schemas from it. Write the view's schema yourself when the
HTTP contract needs stable field names, validation, aliases, or fields that do
not match the table directly:

```python
class UserSchema(fr.IDSchema):
    name: str
    email: str


@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserSchema
```

The four schema attributes have separate jobs:

| Attribute | Used for | Default |
|---|---|---|
| {attr}`schema <fastapi_restly.views.BaseRestView.schema>` | The source that Restly derives the other three from | Constructed from `model` when omitted |
| {attr}`schema_response <fastapi_restly.views.BaseRestView.schema_response>` | `GET` responses and successful write responses | `schema` without `WriteOnly` fields |
| {attr}`schema_create <fastapi_restly.views.BaseRestView.schema_create>` | `POST` request body | `schema` without `ReadOnly` fields |
| {attr}`schema_update <fastapi_restly.views.BaseRestView.schema_update>` | `PATCH` request body | Writable fields from `schema`, made optional |

Both derived schemas also leave out a primary key the server generates
(autoincrement, a column default, or a dataclass field with `init=False`),
marked `ReadOnly` or not. A natural key stays writable.

[Schemas & Validation](howto_custom_schema.md) owns field markers,
aliases, validation, and the choice between explicit and constructed schemas.

For larger applications, define the class without registration side effects
and include it where the app or router is assembled:

```python
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserSchema


fr.include_view(app, UserView)
```

Both registration forms produce the same routes.
[New & Existing Projects](howto_project_structure.md) shows where the direct call belongs in a
multi-package application.

(default-crud-routes)=
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
grammar. [Responses](howto_response_schema.md)
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

(item-route-matching)=
### Item paths and static routes

The built-in `GET`, `PATCH`, and `DELETE` item paths follow
{attr}`id_type <fastapi_restly.views.BaseRestView.id_type>`. By default, it is
inferred from the model's primary key. React Admin's `PUT` uses the same path.

| ID type | Registered item path | Matching values |
|---|---|---|
| `int` | `/{id:int}` | Non-negative digits, including `0` |
| `uuid.UUID` | `/{id:uuid}` | UUIDs accepted by Starlette's converter |
| Any other type | `/{id}` | Any single path segment, then FastAPI validation |

An integer or UUID item route leaves `/users/me` available to a static route
registered after the view. A value that does not match the converter returns
`404` instead of the previous `422`, unless another route handles it. Negative
integer IDs do not match, even if the row exists. OpenAPI still shows
`/users/{id}` with the integer or UUID parameter schema.

Custom endpoint declarations keep their paths. To accept negative integer
IDs, replace each needed item endpoint with an untyped path such as
`@fr.get("/{id}")` and annotate its `id` parameter as `int`. See
[Request Lifecycle & Hooks](customize.md) for endpoint replacements. Register
static routes before untyped item routes, including views with string IDs.

## Configure a view

Configure the class according to the contract the resource needs:

| Requirement | Configuration |
|---|---|
| Stable response fields | Declare {attr}`schema <fastapi_restly.views.BaseRestView.schema>` |
| Different create or update validation | Declare {attr}`schema_create <fastapi_restly.views.BaseRestView.schema_create>` or {attr}`schema_update <fastapi_restly.views.BaseRestView.schema_update>` |
| A different type for the `{id}` path parameter | Set {attr}`id_type <fastapi_restly.views.BaseRestView.id_type>` |
| Read-only or otherwise restricted routes | Set {attr}`exclude_routes <fastapi_restly.views.BaseRestView.exclude_routes>` with `fr.ViewRoute` values |
| Disable pagination | Set {attr}`pagination <fastapi_restly.views.BaseRestView.pagination>` to `None`, or to a {class}`NoPagination <fastapi_restly.views.NoPagination>` to choose its envelope |
| Change page limits, parameter names, or the list envelope | Set {attr}`pagination <fastapi_restly.views.BaseRestView.pagination>` to a {class}`NumberedPagination <fastapi_restly.views.NumberedPagination>` |
| Accept a view-specific list query key | Declare it on the endpoint method or in a dependency, or add it to {attr}`extra_query_params <fastapi_restly.views.BaseRestView.extra_query_params>` when code reads it from the request |
| Apply FastAPI metadata or dependencies to every route | Set {attr}`tags <fastapi_restly.views.View.tags>`, {attr}`responses <fastapi_restly.views.View.responses>`, or {attr}`dependencies <fastapi_restly.views.View.dependencies>` |
| Set a route name, summary, or operation ID | Set {attr}`route_options <fastapi_restly.views.View.route_options>` as shown in [OpenAPI customization](#default-route-metadata) |
| Use another session dependency | Override the `session` annotation with `Annotated[..., Depends(...)]` |

[View Reference](view_reference.md) lists the exact attribute types and
method signatures.
[Use Restly in an Existing Project](howto_existing_project.md)
covers custom engines, session generators, and a per-view session dependency.

(exclude-crud-routes)=

## Exclude CRUD routes

Set {attr}`exclude_routes <fastapi_restly.views.BaseRestView.exclude_routes>` to prevent selected CRUD endpoint methods from being registered as routes:

```python
@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    exclude_routes = [fr.ViewRoute.DELETE, fr.ViewRoute.UPDATE]
```

Valid values are: {attr}`fr.ViewRoute.GET_MANY <fastapi_restly.views.ViewRoute.GET_MANY>`, {attr}`fr.ViewRoute.GET_ONE <fastapi_restly.views.ViewRoute.GET_ONE>`, {attr}`fr.ViewRoute.CREATE <fastapi_restly.views.ViewRoute.CREATE>`, {attr}`fr.ViewRoute.UPDATE <fastapi_restly.views.ViewRoute.UPDATE>`, {attr}`fr.ViewRoute.DELETE <fastapi_restly.views.ViewRoute.DELETE>`. Endpoint-method names such as `"delete_endpoint"` are also accepted; any other string raises `AttributeError` at startup.

An exclusion may use a
`ViewRoute` value or its endpoint-method string, such as `"delete_endpoint"`.

(nested-resources)=

## Nested Resources


A nested resource is a child resource served under its parent in the URL,
such as `/projects/{project_id}/tasks`. In Restly it is an ordinary
{class}`AsyncRestView <fastapi_restly.views.AsyncRestView>` with three
additions: a path parameter in its `prefix`, a dependency that looks up the
parent, and a {attr}`scope <fastapi_restly.views.BaseRestView.scope>` that
limits the rows to that parent. Every CRUD route keeps working, including
paging, filters and sort.

A flat view filtered by the foreign key, `GET /tasks?project_id=17`, needs no
extra code; see [Foreign-key filtering](#foreign-key-filtering). Nest the
view when the nested URL is part of your API.

(nested-one-level)=
### One level

This example serves the tasks of a project at
`/projects/{project_id}/tasks`:

```python
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

import fastapi_restly as fr


class Base(fr.ModelDefaults, DeclarativeBase):
    pass


class Project(fr.IDMixin, Base):
    name: Mapped[str]
    archived: Mapped[bool] = mapped_column(default=False)


class Task(fr.IDMixin, Base):
    title: Mapped[str]
    project_id: Mapped[int] = mapped_column(sa.ForeignKey("project.id"), init=False)


class ProjectSchema(fr.IDSchema):
    name: str


class TaskSchema(fr.IDSchema):
    title: str
    project_id: fr.ReadOnly[int]


class Parent(fr.ContextNamespace):
    project_id: fr.ContextParam[int]


app = FastAPI()
fr.configure(app, async_database_url="sqlite+aiosqlite:///app.db")


@fr.include_view(app)
class ProjectView(fr.AsyncRestView):
    prefix = "/projects"
    model = Project
    schema = ProjectSchema
    scope = fr.where_clause(Project.archived.is_(False))


async def project_from_path(project_id: int, session: fr.AsyncSessionDep) -> int:
    query = fr.apply_clauses(
        sa.select(Project.id).where(Project.id == project_id),
        fr.resolve_scope(ProjectView),
    )
    found = await session.scalar(query)
    if found is None:
        raise fr.exc.NotFound(f"Project with id {project_id} was not found")
    return found


@fr.include_view(app)
class ProjectTaskView(fr.AsyncRestView):
    prefix = "/projects/{project_id}/tasks"
    model = Task
    schema = TaskSchema
    dependencies = [Parent.depends(project_id=project_from_path)]
    scope = fr.all_of(
        fr.resolve_scope(Task),
        fr.where_clause(Task.project_id == Parent.project_id),
    )

    async def create(self, schema_obj):
        task = await self.make_new_object(schema_obj)
        task.project_id = Parent.project_id()
        return await self.save_object(task)
```

`ProjectTaskView` differs from a flat view in these places:

- `prefix` contains `{project_id}`, so every route of the view sits under a
  project.
- `project_from_path` reads `project_id` from the path. FastAPI checks that it
  is an integer and lists it in OpenAPI on every route. The function answers
  `404` when the project does not exist or `ProjectView` hides it.
- {meth}`Parent.depends <fastapi_restly.clauses.ContextNamespace.depends>`
  binds the project id for the request. `Parent` is a
  {class}`fr.ContextNamespace <fastapi_restly.clauses.ContextNamespace>`, like
  `Current` in [Current & ContextParam](#current-request-binding). One `Parent`
  serves every nested view in the app.
- `scope` limits every read to the tasks of that project: the list, its
  total count, get one, update and delete. A task of another project answers
  `404`. A view's scope replaces the model's default scope
  ([View scopes](#view-scope)), so `fr.resolve_scope(Task)` adds it back.
- `create` sets the project id; see [Create a child](#nested-create).

The routes then answer like this:

```text
GET    /projects/1/tasks?sort=-title   the tasks of project 1, by title, descending
POST   /projects/1/tasks               a new task in project 1
GET    /projects/2/tasks/7             404 when task 7 is in another project
GET    /projects/99/tasks              404 when project 99 does not exist
GET    /projects/x/tasks               422, x is not an integer
```

(nested-create)=
### Create a child

The project id comes from the path, not from the request body. `TaskSchema`
marks `project_id` as {data}`fr.ReadOnly <fastapi_restly.schemas.ReadOnly>`,
so the generated create and update schemas leave it out. The `create`
override sets it from `Parent` after
{meth}`make_new_object <fastapi_restly.views.AsyncRestView.make_new_object>`
builds the task.

Declare the foreign key with `init=False`. `make_new_object` builds the task
from the request body, which has no `project_id`. Without `init=False` the
model requires it there, and the create answers `500`.

Keep the parent id out of every write schema of the nested view. If
`TaskSchema` declared a writable `project_id`, the generated update schema would
include it. An [explicit write schema](#explicit-write-schemas) writes every
field it declares. Either way, a `PATCH` could move the task to a project the
URL never named, past the lookup.

Set the parent id in the view, not in a column default that reads
`Parent.project_id`. With such a default, creating a task anywhere else fails,
for example in a flat view or a script, because nothing binds `Parent` there.

(nested-parent-access)=
### Which parents a client can reach

The lookup decides which parents a client can reach. `project_from_path`
applies the scope of `ProjectView` through
{func}`fr.resolve_scope <fastapi_restly.views.resolve_scope>`, so a project
that `GET /projects/{id}` hides also answers `404` here, with all of its tasks.
In the example, that is an archived project.

Without this check, `POST /projects/{project_id}/tasks` creates a task in any
project whose id a client can guess.

The lookup does not run the
{meth}`authorize <fastapi_restly.views.AsyncRestView.authorize>` method of
`ProjectView`. Put rules about which rows a user may see in a scope, so that
both views apply them. The nested view still runs its own `authorize` on each
of its routes.

(nested-deeper)=
### Deeper nesting

For more levels, build a chain of base views, one per level. Each base adds
its segment to `prefix` and its lookup to `dependencies`. Both add up from
base to subclass; see [Concatenate URL prefixes](#prefix-concatenation). This
example serves tasks at `/companies/{company_id}/projects/{project_id}/tasks`.
It is a separate app. It uses the imports, `app`, `Base`, `Task` and
`TaskSchema` of the first example, and its own `Project`, `Parent` and lookups:

```python
from typing import Annotated

from fastapi import Depends


class Company(fr.IDMixin, Base):
    name: Mapped[str]


class Project(fr.IDMixin, Base):
    name: Mapped[str]
    company_id: Mapped[int] = mapped_column(sa.ForeignKey("company.id"), init=False)


class Parent(fr.ContextNamespace):
    company_id: fr.ContextParam[int]
    project_id: fr.ContextParam[int]


async def company_from_path(company_id: int, session: fr.AsyncSessionDep) -> int:
    query = fr.apply_clauses(
        sa.select(Company.id).where(Company.id == company_id),
        fr.resolve_scope(Company),
    )
    found = await session.scalar(query)
    if found is None:
        raise fr.exc.NotFound(f"Company with id {company_id} was not found")
    return found


async def project_from_path(
    project_id: int,
    company_id: Annotated[int, Depends(company_from_path)],
    session: fr.AsyncSessionDep,
) -> int:
    query = fr.apply_clauses(
        sa.select(Project.id).where(
            Project.id == project_id, Project.company_id == company_id
        ),
        fr.resolve_scope(Project),
    )
    found = await session.scalar(query)
    if found is None:
        raise fr.exc.NotFound(f"Project with id {project_id} was not found")
    return found


class CompanyScoped(fr.AsyncRestView):
    prefix = "/companies/{company_id}"
    dependencies = [Parent.depends(company_id=company_from_path)]


class ProjectScoped(CompanyScoped):
    prefix = "/projects/{project_id}"
    dependencies = [Parent.depends(project_id=project_from_path)]


@fr.include_view(app)
class ProjectTaskView(ProjectScoped):
    prefix = "/tasks"
    model = Task
    schema = TaskSchema
    scope = fr.all_of(
        fr.resolve_scope(Task),
        fr.where_clause(Task.project_id == Parent.project_id),
    )

    async def create(self, schema_obj):
        task = await self.make_new_object(schema_obj)
        task.project_id = Parent.project_id()
        return await self.save_object(task)
```

`project_from_path` gets the company id from `company_from_path` as a FastAPI
sub-dependency. FastAPI runs the company lookup first, once per request, and
answers `404` for an unknown company. The project query also checks that the
project belongs to that company, so `/companies/2/projects/1/tasks` answers
`404` when project 1 is in company 1.

Each lookup applies the scope of its parent, as in
[Which parents a client can reach](#nested-parent-access). This example has
no views for companies or projects, so it passes the models. When a view
serves the parent, such as a `ProjectView`, pass the view instead, so the
nested routes hide what that view hides.

Do not read `Parent.company_id()` in `project_from_path` instead. When the
company id in the path is not an integer, FastAPI still runs the project
lookup, `Parent.company_id` is not bound, and the request answers `500`
instead of `422`.

Let each base inherit the one above it, as `ProjectScoped` inherits
`CompanyScoped`. Two mixins side by side,
`class ProjectTaskView(CompanyMixin, ProjectMixin, fr.AsyncRestView)`, add their
segments in reverse order: `/projects/{project_id}/companies/{company_id}/tasks`.

### Things to know

- Name the path segment after the parent, such as `{project_id}`. The item
  routes already use `{id}`, and FastAPI raises an error for the duplicate
  when you include the view.
- A model can have a flat view and nested views at the same time, such as a
  `TaskView` at `/tasks` next to `ProjectTaskView`. Both appear under the
  "Tasks" tag in OpenAPI unless you set
  {attr}`tags <fastapi_restly.views.View.tags>`. Watch two things:
  - Each view applies its own scope. Rows that the scope of `TaskView` hides,
    such as another user's tasks, still show under the nested URL. To hide
    them there too, use `fr.resolve_scope(TaskView)` instead of
    `fr.resolve_scope(Task)` in the scope of `ProjectTaskView`.
  - `TaskView` cannot create tasks with `TaskSchema`, because `project_id` is
    read-only there. Give `TaskView` its own `schema_create`, a
    `fr.BaseSchema` with `project_id: fr.MustExist[int, Project]`. Keep
    `project_id` read-only in `TaskSchema`; see [Create a child](#nested-create).
- OpenAPI marks a field that refers to a task, such as
  `fr.MustExist[int, Task]`, with `x-resource-ref`. Its value comes from the
  prefix of the last view of `Task` that you include. Include the flat view
  after the nested one, so the value is `tasks`. With only a nested view, the
  value is `projects/{project_id}/tasks`, which a client cannot use as a
  resource name.
- A sync {class}`fr.RestView <fastapi_restly.views.RestView>` works the same
  way. Write the lookups as plain functions that take
  {data}`fr.SessionDep <fastapi_restly.db.SessionDep>`, and the `create`
  override as a plain `def` without `await`.
- Each level adds one query to every request.

## Change behavior

A request passes through an endpoint method, a handler, and a business method.
Change the layer that owns the behavior instead of rewriting the entire route:

| Change | Use |
|---|---|
| Stamp or transform data during create or update | Override the business method in [Request Lifecycle & Hooks](customize.md); a server-stamped field is a column default on the model |
| Hide rows from every read | Declare a [scope](scopes.md): `default_scope` on the model, or `scope` on the view |
| Permit or reject an action | Override {meth}`authorize() <fastapi_restly.views.AsyncRestView.authorize>` |
| Run an atomic side effect or a post-commit action | Override `before_action_commit()` or `after_action_commit()` |
| Commit several handlers or custom actions together | Wrap them in {ref}`shared_write_action_commit() <shared-write-action-commit>` |
| Change status, headers, request parameters, or response model | Replace the endpoint method |
| Add a path that is not part of CRUD | Add a method with `@fr.get`, `@fr.post`, or another route decorator |
| Share behavior across resources | Use a [base view](#share-behaviour-with-base-views) or [mixins](#structural-pieces) |
| Replace the list query grammar | Follow [Filtering & Pagination](howto_query_modifiers.md) |

[Request Lifecycle & Hooks](customize.md) owns the lifecycle diagrams, complete
override decision table, and worked recipes.

## Limits and alternatives

The default CRUD contract has these boundaries:

- Resource identity is one scalar primary key on the generated routes. The
  `{id}` path parameter takes the key's Python type, so a UUID or string key
  needs no configuration. Reference fields such as `fr.MustExist[UUID, Model]`
  accept these keys too. A composite key is reached from
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

- [Build a Blog API](tutorial.md) builds and runs a first resource.
- [Request Lifecycle & Hooks](customize.md) changes behavior at the correct layer.
- [Schemas & Validation](howto_custom_schema.md) defines stable request
  and response fields.
- [Views](class_based_views.md) explains registration, shared
  dependencies, and the inheritance model.
- [Guide](user_guide.md) covers individual tasks.
- [View Reference](view_reference.md) lists exact signatures
  and defaults.
