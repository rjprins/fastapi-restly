(nested-resources)=
# Nested Resources

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
## One level

This example serves the tasks of a project at
`/projects/{project_id}/tasks`:

```python
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.orm import Mapped, mapped_column

import fastapi_restly as fr


class Project(fr.IDBase):
    name: Mapped[str]
    archived: Mapped[bool] = mapped_column(default=False)


class Task(fr.IDBase):
    title: Mapped[str]
    project_id: Mapped[int] = mapped_column(sa.ForeignKey("project.id"), init=False)


class ProjectRead(fr.IDSchema):
    name: str


class TaskRead(fr.IDSchema):
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
    schema = ProjectRead
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
    schema = TaskRead
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
  `Current` in [Current context](#current-request-binding). One `Parent`
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
## Create a child

The project id comes from the path, not from the request body. `TaskRead`
marks `project_id` as {data}`fr.ReadOnly <fastapi_restly.schemas.ReadOnly>`,
so the generated create and update schemas leave it out. The `create`
override sets it from `Parent` after
{meth}`make_new_object <fastapi_restly.views.AsyncRestView.make_new_object>`
builds the task.

Declare the foreign key with `init=False`. `make_new_object` builds the task
from the request body, which has no `project_id`. Without `init=False` the
model requires it there, and the create answers `500`.

Keep the parent id out of every write schema of the nested view. If
`TaskRead` declared a writable `project_id`, the generated update schema would
include it. An [explicit write schema](#explicit-write-schemas) writes every
field it declares. Either way, a `PATCH` could move the task to a project the
URL never named, past the lookup.

Set the parent id in the view, not in a column default that reads
`Parent.project_id`. With such a default, creating a task anywhere else fails,
for example in a flat view or a script, because nothing binds `Parent` there.

(nested-parent-access)=
## Which parents a client can reach

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
## Deeper nesting

For more levels, build a chain of base views, one per level. Each base adds
its segment to `prefix` and its lookup to `dependencies`. Both add up from
base to subclass; see [Concatenate URL prefixes](#prefix-concatenation). This
example serves tasks at `/companies/{company_id}/projects/{project_id}/tasks`.
It is a separate app. It uses the imports, `app`, `Task` and `TaskRead` of the
first example, and its own `Project`, `Parent` and lookups:

```python
from typing import Annotated

from fastapi import Depends


class Company(fr.IDBase):
    name: Mapped[str]


class Project(fr.IDBase):
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
    schema = TaskRead
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

## Things to know

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
  - `TaskView` cannot create tasks with `TaskRead`, because `project_id` is
    read-only there. Give `TaskView` its own `schema_create`, a
    `fr.BaseSchema` with `project_id: fr.MustExist[int, Project]`. Keep
    `project_id` read-only in `TaskRead`; see [Create a child](#nested-create).
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

## See also

- [Current context](howto_current.md): binding a value for each request.
- [Scopes](scopes.md): what a view's scope covers, and how it replaces the
  model's default scope.
- [Share Behaviour with Base Views](howto_inheritance.md): how `prefix`,
  `dependencies` and `responses` add up from base to subclass.
