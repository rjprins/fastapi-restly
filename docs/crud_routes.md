# CRUD Routes

{class}`AsyncRestView <fastapi_restly.views.AsyncRestView>` and
{class}`RestView <fastapi_restly.views.RestView>` give one SQLAlchemy model
five CRUD routes. A subclass sets the URL prefix and the model, and it can
also set the schemas. {func}`include_view()
<fastapi_restly.views.include_view>` registers the routes:

```python
@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
```

`UserView` now serves requests to list, create, get, update and delete users
under `/users`. [Build a Blog API](tutorial.md) shows the app, the database
configuration and the model around such a view, in one file that runs.
`include_view` also works as a direct call; see
[Register a view](#register-a-view).

Each route runs an endpoint method, a final handler and a business method.
To change what a route does, you override the endpoint method or the business
method; [Which method do I override](#which-method-to-override) maps each
change to its method.

(choose-async-or-sync)=
## Choose async or sync

Use {class}`AsyncRestView <fastapi_restly.views.AsyncRestView>` for a new
application, unless the application already uses synchronous SQLAlchemy. It
gets an `AsyncSession` through
{data}`fr.AsyncSessionDep <fastapi_restly.db.AsyncSessionDep>`, and the
methods you override are `async def` methods.

Use {class}`RestView <fastapi_restly.views.RestView>` with synchronous
SQLAlchemy sessions, or with libraries that only work synchronously. It gets
a `Session` through {data}`fr.SessionDep <fastapi_restly.db.SessionDep>`,
and the methods you override are plain `def` methods.

Apart from this, the two classes are the same: the same routes, the same
configuration and the same methods to override. You choose for each view, so
one application can use both. [Database & Configuration](database.md) shows
how to configure the sessions. To give one view another session, see
[Use another database on one view](#session-dependency-one-view).
The examples on this page use `AsyncRestView`.

(default-crud-routes)=
(default-crud-behavior)=
## The default routes

For `prefix = "/users"`, `include_view` registers these routes:

| Request | Endpoint method | Input | Response | Status |
|---|---|---|---|---|
| `GET /users` | {meth}`get_many_endpoint <fastapi_restly.views.AsyncRestView.get_many_endpoint>` | Filter, sort and page parameters | A page of objects in a `data` envelope | `200` |
| `POST /users` | {meth}`create_endpoint <fastapi_restly.views.AsyncRestView.create_endpoint>` | `schema_create` | `schema_response` | `201` |
| `GET /users/{id}` | {meth}`get_one_endpoint <fastapi_restly.views.AsyncRestView.get_one_endpoint>` | The id in the path | `schema_response` | `200` |
| `PATCH /users/{id}` | {meth}`update_endpoint <fastapi_restly.views.AsyncRestView.update_endpoint>` | `schema_update` | `schema_response` | `200` |
| `DELETE /users/{id}` | {meth}`delete_endpoint <fastapi_restly.views.AsyncRestView.delete_endpoint>` | The id in the path | Empty body | `204` |

The main path of the list is `/users`, without a slash at the end. Restly
also accepts `/users/`, as a route that OpenAPI does not show.

`GET /users` takes the filter, sort and page parameters that
[Filtering & Pagination](filtering.md) defines, and it is paginated by
default.
[Responses](responses.md) shows the response bodies and how to
change the `data` envelope.

A row that does not exist returns `404`, and so does a row that the view's
[scope](scopes.md) hides. Each write commits before Restly builds the
response, and a read does not commit; see
[What commits, and when](#what-commits).

:::{important}
Restly does not authenticate requests, and by default it allows every action
on every route. Add authentication with FastAPI dependencies; see
[Run a dependency on every route](#run-a-dependency-on-every-route). Then
decide per action who may do what in
{meth}`authorize() <fastapi_restly.views.AsyncRestView.authorize>`; see
[Authorization](#authorize-gate).
:::

(item-route-matching)=
### Item paths and static routes

The paths of the built-in `GET`, `PATCH` and `DELETE` routes for one row
follow {attr}`id_type <fastapi_restly.views.BaseRestView.id_type>`. By
default, Restly takes it from the model's primary key. The `PUT` route of a
React Admin view uses the same path.

| ID type | Registered item path | Matching values |
|---|---|---|
| `int` | `/{id:int}` | Digits only, including `0`; no minus sign |
| `uuid.UUID` | `/{id:uuid}` | UUIDs that Starlette's converter accepts |
| Any other type | `/{id}` | Any single path segment, then FastAPI validation |

With an integer or UUID item path, a static route such as `/users/me` still
works when you register it after the view.

An untyped item path, such as the path for string ids, also matches
`/users/me`. Register a static route like that before the item route.

:::{admonition} Details
:class: details

A value that does not match the converter returns `404`, unless another
route matches it. So a negative integer id does not match, even when the row
exists. OpenAPI still shows the path as `/users/{id}`, with an integer or UUID
parameter.

A route that you declare yourself keeps the path you give it. To accept
negative integer ids, replace each item endpoint you need with an untyped
path such as `@fr.get("/{id}")`, and annotate its `id` parameter as `int`;
see [Change the status code or headers of one route](#replace-endpoint-method).
:::

(crud-model-and-schemas)=
## Set the model and the schemas

A CRUD view sets two class attributes:

- {attr}`prefix <fastapi_restly.views.View.prefix>`: the URL prefix of every
  route of the view.
- {attr}`model <fastapi_restly.views.BaseRestView.model>`: the SQLAlchemy
  mapped class that the routes read and write.

Normal models built on SQLAlchemy's `DeclarativeBase` work. Restly's model
defaults, {class}`fr.ModelDefaults <fastapi_restly.models.ModelDefaults>`, are
optional; see [Models](models.md).

If the view does not declare {attr}`schema
<fastapi_restly.views.BaseRestView.schema>`, Restly generates the view's
schema from `model` when you register the view. From the view's schema, it
derives the request bodies and the response class:
{attr}`schema_create <fastapi_restly.views.BaseRestView.schema_create>`,
{attr}`schema_update <fastapi_restly.views.BaseRestView.schema_update>` and
{attr}`schema_response <fastapi_restly.views.BaseRestView.schema_response>`.
[Generate the schema or write it](#write-or-generate) shows when to write the
schema yourself, and
[Hide a field from requests or from responses](#readonly-and-writeonly)
shows which fields each class takes.

(configure-a-crud-view)=
## Configure a CRUD view

This table maps each need to its setting:

| You need | Configuration |
|---|---|
| Stable response fields | Declare {attr}`schema <fastapi_restly.views.BaseRestView.schema>` |
| Other fields or validation on create or update | Declare {attr}`schema_create <fastapi_restly.views.BaseRestView.schema_create>` or {attr}`schema_update <fastapi_restly.views.BaseRestView.schema_update>` ([Explicit write schemas](#explicit-write-schemas)) |
| Another type for the `{id}` path parameter | Set {attr}`id_type <fastapi_restly.views.BaseRestView.id_type>` ([Item paths](#item-route-matching)) |
| Fewer routes, such as a read-only view | Set {attr}`exclude_routes <fastapi_restly.views.BaseRestView.exclude_routes>` ([Exclude CRUD routes](#exclude-crud-routes)) |
| Hide rows from every read | Set {attr}`scope <fastapi_restly.views.BaseRestView.scope>`, or `default_scope` on the model ([Scopes](#view-scope)) |
| No pagination | Set {attr}`pagination <fastapi_restly.views.BaseRestView.pagination>` to `None`, or to a {class}`NoPagination <fastapi_restly.views.NoPagination>` to choose its envelope ([Filtering & Pagination](#no-pagination)) |
| Other page limits, parameter names or list envelope | Set {attr}`pagination <fastapi_restly.views.BaseRestView.pagination>` to a {class}`NumberedPagination <fastapi_restly.views.NumberedPagination>` ([Pagination settings](#pagination-settings)) |
| Fewer filter or sort keys on a list | Set {attr}`include_filters <fastapi_restly.views.BaseRestView.include_filters>` or {attr}`include_sort_keys <fastapi_restly.views.BaseRestView.include_sort_keys>` ([Choose the keys of a list](#choose-list-keys)) |
| A list filter of your own | Decorate a method with {func}`fr.list_filter <fastapi_restly.views.list_filter>` ([Add a filter of your own](#own-list-filter)) |
| A list query key of your own that is not a filter | Declare it on the endpoint method or in a dependency. When code reads it from the request, add it to {attr}`extra_query_params <fastapi_restly.views.BaseRestView.extra_query_params>` |
| OpenAPI metadata or dependencies on every route | Set {attr}`tags <fastapi_restly.views.View.tags>`, {attr}`responses <fastapi_restly.views.View.responses>` or {attr}`dependencies <fastapi_restly.views.View.dependencies>` ([Share behavior with base views](#share-behaviour-with-base-views)) |
| A route name, summary or operation ID | Set {attr}`route_options <fastapi_restly.views.View.route_options>` ([OpenAPI](#route-options)) |
| Another session for one view | Override the `session` annotation ([Database & Configuration](#session-dependency-one-view)) |

[View Reference](#view-class-attributes) lists the type and the default of
each attribute.

(exclude-crud-routes)=
## Exclude CRUD routes

Set {attr}`exclude_routes <fastapi_restly.views.BaseRestView.exclude_routes>`
to leave out some of the CRUD routes:

```python
@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    exclude_routes = [fr.ViewRoute.DELETE, fr.ViewRoute.UPDATE]
```

`UserView` now serves only `GET /users`, `POST /users` and `GET /users/{id}`.

Subclasses inherit `exclude_routes`. To make several views read-only, set it
on a base view:

```python
class ReadOnlyView(fr.AsyncRestView):
    exclude_routes = (fr.ViewRoute.CREATE, fr.ViewRoute.UPDATE, fr.ViewRoute.DELETE)


@fr.include_view(app)
class ProductView(ReadOnlyView):
    prefix = "/products"
    model = Product
```

`ProductView` only serves `GET /products` and `GET /products/{id}`.

A subclass that sets its own `exclude_routes` replaces the list of its base. It
does not add to it. So repeat the entries of the base, or the subclass serves
the write routes again:
`exclude_routes = (*ReadOnlyView.exclude_routes, fr.ViewRoute.GET_ONE)`.

:::{admonition} Details
:class: details

Each entry is a {class}`fr.ViewRoute <fastapi_restly.views.ViewRoute>` value,
`GET_MANY`, `GET_ONE`, `CREATE`, `UPDATE` or `DELETE`, or the name of a route
method, such as `"delete_endpoint"`. A name that is not a route of the view
raises `AttributeError` when you register the view.
:::

(nested-resources)=
## Nest a resource under its parent

A nested resource is a child resource that the URL places under its parent,
such as `/projects/{project_id}/tasks`. In Restly, it is a normal
{class}`AsyncRestView <fastapi_restly.views.AsyncRestView>` with three
additions: a path parameter in its `prefix`, a dependency that looks up the
parent, and a {attr}`scope <fastapi_restly.views.BaseRestView.scope>` that
limits the rows to that parent. Every CRUD route keeps working, including
pages, filters and sorting.

A flat view that filters on the foreign key, `GET /tasks?project_id=17`,
needs no extra code; see [Filter by a foreign key](#foreign-key-filtering).
Nest the view when the nested URL is part of your API.

(nested-one-level)=
### Nest one level

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

- `prefix` contains `{project_id}`, so every route of the view is under a
  project. Name the path parameter after the parent, as here. The item routes
  already use `{id}`, and FastAPI raises an error for a second `{id}` when you
  register the view.
- `project_from_path` reads `project_id` from the path. FastAPI checks that it
  is an integer, and OpenAPI lists it on every route. The function returns
  `404` when the project does not exist or when `ProjectView` hides it.
- {meth}`Parent.depends <fastapi_restly.clauses.ContextNamespace.depends>`
  binds the project id for the request. `Parent` is a
  {class}`fr.ContextNamespace <fastapi_restly.clauses.ContextNamespace>`, like
  `Current` in [Request Context](#current-request-binding). One `Parent`
  serves every nested view in the app.
- `scope` limits every read to the tasks of that project: the list, its
  total count, get one, update and delete. A task of another project returns
  `404`. A view's scope replaces the model's default scope
  ([Give one view other rows](#view-scope)), so `fr.resolve_scope(Task)`
  adds the default scope back.
- `create` sets the project id; see [Create a child](#nested-create).

The routes then answer like this:

```text
GET    /projects/1/tasks?sort=-title   the tasks of project 1, by title, descending
POST   /projects/1/tasks               a new task in project 1
GET    /projects/2/tasks/7             404 when task 7 is in another project
GET    /projects/99/tasks              404 when project 99 does not exist
GET    /projects/x/tasks               422, x is not an integer
```

:::{admonition} Details
:class: details

A sync {class}`fr.RestView <fastapi_restly.views.RestView>` works in the same
way. Write the lookups as plain functions that take
{data}`fr.SessionDep <fastapi_restly.db.SessionDep>`, and write the `create`
override as a plain `def` without `await`.
:::

(nested-create)=
### Create a child

The project id comes from the path, not from the request body. `TaskSchema`
marks `project_id` as {data}`fr.ReadOnly <fastapi_restly.schemas.ReadOnly>`,
so the generated create and update schemas leave it out. The `create`
override sets it from `Parent` after
{meth}`make_new_object <fastapi_restly.views.AsyncRestView.make_new_object>`
builds the task.

Declare the foreign key with `init=False`. `make_new_object` builds the task
from the request body, which has no `project_id`. Without `init=False`, the
model requires `project_id` in its constructor, and the create returns `500`.

Keep the parent id out of every write schema of the nested view. If
`TaskSchema` declared a writable `project_id`, the generated update schema
would include it. An [explicit write schema](#explicit-write-schemas) writes
every field it declares. In both cases, a `PATCH` request could move the task
to a project that the URL never named, and the lookup would not check it.

:::{admonition} Details
:class: details

Set the parent id in the view, not in a column default that reads
`Parent.project_id`. Only the nested route knows the parent, so the value
belongs in `create`; see [Values every write path sets](#write-path-values)
for the difference. With such a default, creating a task anywhere else
fails, for example in a flat view or a script, because nothing binds
`Parent` there.
:::

(nested-parent-access)=
### Which parents a client can reach

The lookup decides which parents a client can reach. `project_from_path`
applies the scope of `ProjectView` through
{func}`fr.resolve_scope <fastapi_restly.views.resolve_scope>`. So a project
that `GET /projects/{id}` hides also returns `404` here, with all of its
tasks. In the example, that is an archived project.

Without this check, `POST /projects/{project_id}/tasks` creates a task in any
project whose id a client can guess.

The lookup does not run the
{meth}`authorize <fastapi_restly.views.AsyncRestView.authorize>` method of
`ProjectView`. Put rules about which rows a user may see in a scope, so that
both views apply them. The nested view still runs its own `authorize` on each
of its routes.

(nested-deeper)=
### Nest more than one level

For more levels, build a chain of base views, one for each level. Each base
adds its segment to `prefix` and its lookup to `dependencies`. Both add up
from base to subclass; see [Concatenate URL prefixes](#prefix-concatenation).
This example serves tasks at
`/companies/{company_id}/projects/{project_id}/tasks`. It is a separate app.
It uses the imports, `app`, `Base`, `Task` and `TaskSchema` of the first
example, and its own `Project`, `Parent` and lookups:

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
returns `404` for an unknown company. The project query also checks that the
project belongs to that company. So `/companies/2/projects/1/tasks` returns
`404` when project 1 is in company 1.

Each lookup applies the scope of its parent, as in
[Which parents a client can reach](#nested-parent-access). This example has
no views for companies or projects, so it passes the models. When a view
serves the parent, such as a `ProjectView`, pass the view instead. The nested
routes then hide what that view hides.

:::{admonition} Details
:class: details

Do not read `Parent.company_id()` in `project_from_path` instead. When the
company id in the path is not an integer, FastAPI still runs the project
lookup. `Parent.company_id` is then not bound, and the request returns `500`
instead of `422`.

Let each base inherit the one above it, as `ProjectScoped` inherits
`CompanyScoped`. Two mixins side by side,
`class ProjectTaskView(CompanyMixin, ProjectMixin, fr.AsyncRestView)`, add their
segments in reverse order: `/projects/{project_id}/companies/{company_id}/tasks`.

Each level adds one query to every request.
:::

(nested-and-flat-views)=
### A flat view next to the nested one

A model can have a flat view and nested views at the same time, such as a
`TaskView` at `/tasks` next to `ProjectTaskView`. Watch these points:

- Each view applies its own scope. Rows that the scope of `TaskView` hides,
  such as the tasks of other users, still show under the nested URL. To hide
  them there too, use `fr.resolve_scope(TaskView)` instead of
  `fr.resolve_scope(Task)` in the scope of `ProjectTaskView`.
- `TaskView` cannot create tasks with `TaskSchema`, because `project_id` is
  read-only there. Give `TaskView` its own `schema_create`, a
  `fr.BaseSchema` with `project_id: fr.MustExist[int, Project]`. Keep
  `project_id` read-only in `TaskSchema`; see [Create a child](#nested-create).
- Both views appear under the "Tasks" tag in OpenAPI, unless you set
  {attr}`tags <fastapi_restly.views.View.tags>`.
- OpenAPI marks a field that refers to a task, such as
  `fr.MustExist[int, Task]`, with `x-resource-ref`. Its value comes from the
  prefix of the last view of `Task` that you register. Register the flat view
  after the nested one, so the value is `tasks`. With only a nested view, the
  value is `projects/{project_id}/tasks`, which a client cannot use as a
  resource name.

(crud-limits)=
## What the default routes do not do

The default CRUD routes have these limits:

- A row is identified by one primary key column. The `{id}` path parameter
  takes the Python type of the key, so a UUID or string key needs no
  configuration. Reference fields such as `fr.MustExist[UUID, Model]` accept
  these keys too. For a composite key, write a custom route that loads the
  row by a condition ([Look a row up by another key](#natural-key-route)), or
  use a {class}`View <fastapi_restly.views.View>` with explicit paths.
- Responses can contain nested objects, and lists can filter on
  relationships. Request bodies with nested objects are not supported in
  general. Use `MustExist`, `IDRef` or `IDSchema` to refer to other rows, or
  change the payload in a business method; see
  [Relationships](relationships.md).
- The update route uses `PATCH`, not `PUT`. Add a `PUT` route only when the
  client needs one.

## See also

- [Build a Blog API](tutorial.md): build and run a first resource.
- [Request Lifecycle & Hooks](lifecycle.md): change what a route does.
- [Schemas & Validation](schemas.md): the fields that the routes
  accept and return.
- [Views](views.md): registration, shared dependencies and base
  views.
- [View Reference](view_reference.md): the exact signatures and defaults.
