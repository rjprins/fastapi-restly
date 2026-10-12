# Models

Restly works with SQLAlchemy models on a declarative base of your own. The
base is your class, with its own registry and `MetaData`, as in any
SQLAlchemy project. Restly does not own it. Restly's views and schemas work
with any declarative model, and Restly gives two kinds of optional help:

- {class}`fr.ModelDefaults <fastapi_restly.models.ModelDefaults>` adds
  Restly's defaults to your base.
- {class}`fr.IDMixin <fastapi_restly.models.IDMixin>` and
  {class}`fr.TimestampsMixin <fastapi_restly.models.TimestampsMixin>` add
  common columns to a model.

The model is also the place for the rules that every write must follow. A
view is only one way to write a row. Scripts, jobs, imports and the
{mod}`fr.objects <fastapi_restly.objects>` helpers write rows too, and they
never run a view. SQLAlchemy runs a column default or a mapper event when it
writes the row, whatever code built the row. So the model sets each value
that every write path needs, such as the author or the tenant. A value that
only the HTTP API sets belongs in the view's
[`create` or `update`](#business-methods).

[Where tenant, audit and soft-delete rules go](#structural-pieces) lists
these rules. The
[SaaS example](https://github.com/rjprins/fastapi-restly/tree/main/example-projects/saas)
runs all of them.

(model-defaults)=

## Add Restly's defaults to your base

Put {class}`fr.ModelDefaults <fastapi_restly.models.ModelDefaults>` in your
base class, before `DeclarativeBase`:

```python
from sqlalchemy import ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

import fastapi_restly as fr


class Base(fr.ModelDefaults, DeclarativeBase):
    pass


class BlogPost(fr.IDMixin, Base):
    published: Mapped[bool] = mapped_column(default=False)
    title: Mapped[str]


class Comment(fr.TimestampsMixin, fr.IDMixin, Base):
    content: Mapped[str]
    blog_post_id: Mapped[int] = mapped_column(ForeignKey("blog_post.id"))
```

`fr.ModelDefaults` already includes SQLAlchemy's `MappedAsDataclass` and
`AsyncAttrs`, so leave those out of the base. Every model on `Base` then gets
these defaults:

- **A dataclass constructor.** The constructor takes keyword arguments only:
  `BlogPost(title="Hello")`. So a field with a default can come before a field
  without one, as `published` does above, and type checkers accept that. Use
  `mapped_column(init=False)` for a column that the constructor must not
  take.
- **Equality by identity.** Two model objects are equal only when they are
  the same object, as for any SQLAlchemy model. So a model object can go in a
  `set`, and comparing objects never loads a relationship.
- **A table name.** The table name is the class name in snake_case:
  `BlogPost` gets `blog_post`. That is why `ForeignKey("blog_post.id")` works.
  Set `__tablename__` on a model to choose another name.
- **Datetimes with a time zone.** `Mapped[datetime]` maps to
  `DateTime(timezone=True)`. Store UTC times in it. PostgreSQL returns them
  with a time zone, but SQLite returns them without one. For a clock time
  without a time zone, use `mapped_column(DateTime())` on that column.
- **Enums as strings.** A column typed with an `enum.Enum` stores the name of
  the member, such as `RED`, as a string of up to 64 characters. It is not a
  database enum type, so a new member needs no database migration.
- **`awaitable_attrs`.** Code outside a view can await a relationship that is
  not loaded yet, as
  [Return related objects in a response](#relationship-loading) shows.

(change-model-defaults)=

## Change a default

The base is your class, so you change it as SQLAlchemy documents. To give
constraints predictable names, which Alembic needs to drop them later, set
`metadata`. To change how a Python type maps to a column type, set
`type_annotation_map`:

```python
from decimal import Decimal

from sqlalchemy import MetaData, Numeric
from sqlalchemy.orm import DeclarativeBase

import fastapi_restly as fr


class Base(fr.ModelDefaults, DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )
    type_annotation_map = {Decimal: Numeric(12, 2)}
```

Your `type_annotation_map` entries win over Restly's. Restly's entries still
apply to the types you leave out. So `{datetime: DateTime()}` stores the
datetimes on this base without a time zone, and enums are still strings. The
columns of `fr.TimestampsMixin` keep their time zone, because the mixin sets
their type itself.

For one column, pass the type to `mapped_column`, as in
`mapped_column(Enum(Color, native_enum=True))`. For one table, set
`__tablename__`.

Set dataclass options on each model, not on the base. For example,
`class Post(fr.IDMixin, Base, eq=True)` makes two `Post` objects equal when
their fields are equal. SQLAlchemy takes the options from the first dataclass
class in the bases of a model, and that is often a mixin. So options on the
base would apply to some models only, and the base raises
{class}`fr.exc.RestlyConfigurationError <fastapi_restly.exc.RestlyConfigurationError>`.

(model-mixins)=

## Add common columns

{class}`fr.IDMixin <fastapi_restly.models.IDMixin>` adds `id`, an integer
primary key. The database chooses its value, so the constructor does not take
it.

{class}`fr.TimestampsMixin <fastapi_restly.models.TimestampsMixin>` adds
`created_at` and `updated_at`. Both are UTC datetimes with a time zone.
The constructor sets both to the current time, and SQLAlchemy sets
`updated_at` again on every update. A schema can return them with
{class}`fr.TimestampsSchemaMixin <fastapi_restly.schemas.TimestampsSchemaMixin>`.

For a different primary key, declare `id` yourself instead of using
`fr.IDMixin`:

```python
from uuid import UUID, uuid4

from sqlalchemy.orm import Mapped, mapped_column


class Product(Base):
    id: Mapped[UUID] = mapped_column(
        primary_key=True, init=False, default_factory=uuid4
    )
    name: Mapped[str]
```

The two mixins are SQLAlchemy dataclass mixins. On a base without
`fr.ModelDefaults`, SQLAlchemy makes each model that uses them a dataclass.
The constructor of that model then takes keyword arguments only, and requires
every field without a default.

Your own mixins with columns, for a base with `fr.ModelDefaults`, must be
dataclass mixins too. Subclass `MappedAsDataclass` and pass `kw_only=True`, as
`AuditStamped` [below](#server-stamped-fields) does. SQLAlchemy warns about a
mixin that is not a dataclass, and SQLAlchemy 2.1 rejects it.

## Use the base in your app

Everything that asks for your models' tables takes the base:

- {func}`fr.db.async_create_all(Base) <fastapi_restly.db.async_create_all>`
  creates the tables during development.
- `fr.testing.configure_tests(base=Base)` tells the test setup which tables
  to clean between tests, and to create with `create_all=True`.
  [Testing](howto_testing.md) shows it.
- Alembic's `env.py` sets `target_metadata = Base.metadata`, as
  [Deploying](deploying.md) shows.

A base only knows the models that have been imported.
[New & Existing Projects](projects.md) shows how one module imports
them all.

(existing-models)=

## Use your existing models

Models that you already have can stay on their own base. Restly's views and
schemas work with any SQLAlchemy declarative model, dataclass or not:

```python
import fastapi_restly as fr
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class AppBase(DeclarativeBase):
    pass


class World(AppBase):
    __tablename__ = "world"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    message: Mapped[str]


@fr.include_view(app)
class WorldView(fr.AsyncRestView):
    prefix = "/world"
    model = World
```

The view gives `World` the default CRUD routes and
[schemas generated from the model](#auto-generated-schemas). Pass the same
base wherever Restly asks for your models' tables, as in
`fr.db.async_create_all(AppBase)` and
`fr.testing.configure_tests(base=AppBase)`.

You can add `fr.ModelDefaults` to such a base later. It changes the models
that the base already has:

- Their constructors take keyword arguments only, and require every field
  without a default.
- `Mapped[datetime]` columns get a time zone. On PostgreSQL, `TIMESTAMP`
  becomes `TIMESTAMP WITH TIME ZONE`. The migration must say how to read the
  stored values, as [Deploying](#timezone-migration) shows.
- Enum columns become strings. On PostgreSQL, a native `ENUM` becomes
  `VARCHAR(64)`, so that needs a migration too.

Alembic shows these column changes when it generates the migration. Review
the migration before you run it.

Put `fr.ModelDefaults` on the base itself. On a class below another
declarative base, it raises
{class}`fr.exc.RestlyConfigurationError <fastapi_restly.exc.RestlyConfigurationError>`,
because Restly's defaults change the registry of the whole base.

(structural-pieces)=

## Where tenant, audit and soft-delete rules go

Each rule goes where it reaches every write or every read:

| Rule | Where it goes |
|---|---|
| A value that every write path must set, such as a slug from the title | A column default or a mapper event on the model: [Set a value on every write path](#write-path-values) |
| A value that the server owns, such as the author | A column default that reads `Current`: [Stamp the fields the server owns](#server-stamped-fields) |
| Each tenant reads and writes only its own rows | A session listener and a stamped column: [Keep each tenant's rows apart](#tenant-row-scoping) |
| Deleted rows are hidden from every read | The model's [default scope](#default-scope) |
| `DELETE` marks the row instead of removing it | A view mixin that overrides `delete`: [Soft-delete rows](#soft-delete-mixin) |

The SaaS example has these rules as mixins. The model mixins `TenantOwned`,
`AuditStamped` and `SoftDeletable` are in
[`app/models.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/models.py),
and the view mixin `SoftDeleteMixin` is in
[`app/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/views.py).
Copy them into your project as a starting point.

(write-path-values)=

## Set a value on every write path

To decide where a value is set, ask which write paths must set it:

- **Every write path**: the views, scripts, jobs, imports and the
  `fr.objects` helpers. Put the value on the model, as a column default or a
  mapper event.
- **Only the HTTP API**: put the value in the view's
  [`create` or `update`](#business-methods), such as a password hash from the
  request body.

A view cannot set a value for every write path. A `create` override often
builds the row itself, without `super()`. Then a value that a view mixin sets
in `create` is lost. The [domain utilities](#domain-utilities)
`make_new_object`, `update_object` and `save_object` are final, so a view
cannot change them either. And a script that builds a row, or calls the
`fr.objects` helpers, never runs a view.

A value from `Current`, such as the author, is a column default:
[Stamp the fields the server owns](#server-stamped-fields) shows how. A value
computed from other columns, such as a slug from the title, is a SQLAlchemy
`before_insert` mapper event on the model:

```python
from sqlalchemy import event
from sqlalchemy.orm import Mapped, mapped_column

import fastapi_restly as fr


class Article(fr.IDMixin, Base):
    title: Mapped[str]
    slug: Mapped[str] = mapped_column(init=False)


@event.listens_for(Article, "before_insert")
def _set_slug(mapper, connection, target):
    target.slug = slugify(target.title)  # slugify is your own function
```

SQLAlchemy runs the event during the flush, just before it inserts the row.
If the slug must change when the title changes, listen to `before_update`
too. Mark `slug` as {data}`fr.ReadOnly <fastapi_restly.schemas.ReadOnly>` on
the schema, so the request body cannot send it.

A mapper event may change only the row that it receives, and it may run SQL
on `connection`. To change other rows in the same flush, use the session's
`before_flush` event. SQLAlchemy's
[mapper events documentation](https://docs.sqlalchemy.org/en/20/orm/events.html#mapper-events)
explains these rules.

(server-stamped-fields)=

## Stamp the fields the server owns

The server decides some fields, such as who created a row and who changed it
last. Make each one a column default that reads a member of your
[`Current`](howto_current.md). This mixin stamps both fields on each model
that uses it:

```python
from sqlalchemy import ForeignKey, orm

import fastapi_restly as fr

from app.current import Current


class AuditStamped(orm.MappedAsDataclass, kw_only=True):
    created_by_id: orm.Mapped[int | None] = orm.mapped_column(
        ForeignKey("user.id"), init=False, insert_default=Current.user_id
    )
    updated_by_id: orm.Mapped[int | None] = orm.mapped_column(
        ForeignKey("user.id"),
        init=False,
        insert_default=Current.user_id,
        onupdate=Current.user_id,
    )


class Document(AuditStamped, fr.TimestampsMixin, fr.IDMixin, Base):
    title: orm.Mapped[str]
```

Each argument has one job:

- `insert_default` gives the value for a new row. On a base with
  `fr.ModelDefaults`, `default` is the default of the constructor, so the
  value for the `INSERT` goes in `insert_default`.
- `onupdate` gives the value for each `UPDATE` of the row. SQLAlchemy sends
  an `UPDATE` only when a column of the row has changed.
- `init=False` keeps the field out of the constructor.
- `Current.user_id` has no parentheses. SQLAlchemy calls it when it writes
  the row, as
  [Read the current user in a column default](#current-column-defaults)
  explains.

Then mark each field
{data}`fr.ReadOnly <fastapi_restly.schemas.ReadOnly>` on the view's schema,
and leave it out of any [explicit write schema](#explicit-write-schemas):

```python
class DocumentSchema(fr.TimestampsSchemaMixin, fr.IDSchema):
    title: str
    created_by_id: fr.ReadOnly[int | None] = None
    updated_by_id: fr.ReadOnly[int | None] = None
```

`init=False` does not protect the field from the request body. Restly sets
each writable field that the constructor does not take after it builds the
object. So without `fr.ReadOnly`, a client could choose the author, and the
column default would not run. With `fr.ReadOnly`, the value always comes from
`Current`.

A column default runs on every write path: a view's `create`, a custom route
that builds the row itself, the `fr.objects` helpers, a bulk route, a script.
Each of them must have `Current` bound when it flushes. If the member is not
bound, the flush raises SQLAlchemy's `StatementError` around a `LookupError`.
It never writes `None` instead. A script or a job binds the values around its
writes, as [Bind in a script or job](#current-manual-binding) shows:

```python
with Current.bind(user_id=1):
    session.add(Document(title="Imported"))
    await session.flush()
```

Make the columns nullable if some rows have no author. In the SaaS example,
the first admin comes from a migration. A migration writes through its own
table objects and runs no ORM default, so that row has no creator. The SaaS
example runs these stamps as `AuditStamped` in
[`app/models.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/models.py).

### Two foreign keys to the same table

`created_by_id` and `updated_by_id` both point at `user.id`. A model can
already have a relationship to `User`, such as `assignee` on `Task`. Then
SQLAlchemy cannot tell which foreign key that relationship uses, and it raises
`AmbiguousForeignKeysError`. Name the foreign key on the relationship:

```python
class Task(AuditStamped, fr.TimestampsMixin, fr.IDMixin, Base):
    assignee_id: orm.Mapped[int] = orm.mapped_column(ForeignKey("user.id"))
    assignee: orm.Mapped[User] = orm.relationship(foreign_keys="Task.assignee_id")

    # AuditStamped adds two more foreign keys to user.id.
    # Without foreign_keys="...", the assignee relationship is ambiguous.
```

(tenant-row-scoping)=

## Keep each tenant's rows apart

In a multi-tenant app, each organization reads and writes only its own rows.
The rule has two halves: a filter on every read, and a column that every write
stamps. Both read the tenant from `Current`.

The tenant rule is not a [scope](#default-scope). A view or a route can
replace its scope, but the tenant rule must hold under every scope and for
every reference check. So it lives on the SQLAlchemy session. A
`do_orm_execute` listener adds a
[`with_loader_criteria`](https://docs.sqlalchemy.org/en/20/orm/queryguide/api.html#sqlalchemy.orm.with_loader_criteria)
option to every ORM read of an entity or a relationship:

```python
import sqlalchemy as sa
from sqlalchemy import ForeignKey, orm

import fastapi_restly as fr


class Current(fr.ContextNamespace):
    org_id: fr.ContextParam[int]
    user_id: fr.ContextParam[int]
    is_admin: fr.ContextParam[bool]


class Base(fr.ModelDefaults, orm.DeclarativeBase):
    pass


class TenantOwned(orm.MappedAsDataclass, kw_only=True):
    """The tenant column: the organization the request acts in."""

    organization_id: orm.Mapped[int] = orm.mapped_column(
        ForeignKey("organization.id"), init=False, insert_default=Current.org_id
    )


# Read identity when the SQL executes, not when the option is attached.
# bindparam tests its callable's truth value, which a Current member rejects,
# so these two wrap the member in a lambda.
tenant_org_id = sa.bindparam(
    "tenant_org_id", callable_=lambda: Current.org_id(), type_=sa.Integer
)
tenant_is_admin = sa.bindparam(
    "tenant_is_admin", callable_=lambda: Current.is_admin(), type_=sa.Boolean
)


class TenantCriteria(orm.UserDefinedOption):
    """Marks a statement that already carries the tenant criteria."""

    propagate_to_loaders = True


@sa.event.listens_for(orm.Session, "do_orm_execute")
def _restrict_tenant_rows(state: orm.ORMExecuteState) -> None:
    if not state.is_select or state.is_column_load:
        return
    if any(isinstance(o, TenantCriteria) for o in state.user_defined_options):
        return
    state.statement = state.statement.options(
        TenantCriteria(),
        orm.with_loader_criteria(
            TenantOwned,
            lambda cls: sa.or_(tenant_is_admin, cls.organization_id == tenant_org_id),
            include_aliases=True,
        ),
    )


class Project(TenantOwned, fr.TimestampsMixin, fr.IDMixin, Base):
    name: orm.Mapped[str]
```

The column is the write half. `init=False` keeps the tenant out of the
constructor, and `fr.ReadOnly` on the schema keeps it out of the request body.
The insert default stamps it during the flush, from the same member that the
listener reads. So a row lands in the organization that the request acts in,
whatever code builds it. An admin who writes into another tenant acts as that
tenant, and the stamp follows. Code that needs the tenant before the flush,
such as a mapper event or a check for a unique name, reads `Current.org_id()`
itself, because the insert default has not run yet.

Bind the values once per request with a generated dependency,
`Current.depends(...)`, as
[Bind from a FastAPI dependency](#current-request-binding) shows. The SaaS
example binds them in
[`app/current.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/current.py).
Its sources answer 401 to a request without an identity, so the values are
never `None`. An admin is a flag, `Current.is_admin`, not a missing value.

### What the listener covers

- The listener adds the option also to a query that starts from a model
  without a tenant, because that query can load tenant rows through a
  relationship. `select(Organization).options(joinedload(Organization.users))`
  must filter the users.
- Relationship loads get the option too, also when their parent was inserted
  and not loaded by a query. Keep the default `propagate_to_loaders=True`, so
  that joined eager loads get the criteria.
- A relationship load receives the options of the statement that loaded its
  parent. Without the `TenantCriteria` marker, the listener would add a second
  copy of each criterion to that load. A chain of loads, such as the subtasks
  of a task, would add one more copy at each level. The marker goes with the
  criteria, so the listener skips a statement that has it.
- [`bindparam(callable_=...)`](https://docs.sqlalchemy.org/en/20/core/sqlelement.html#sqlalchemy.sql.expression.bindparam)
  reads the identity only when the SQL uses that parameter. So a query that
  reads no tenant rows needs no identity. A read of tenant rows without an
  identity fails with a `LookupError`, wrapped in SQLAlchemy's
  `StatementError`. The lambdas are needed because `bindparam` tests whether
  its callable is true, and a `ContextParam` raises on that test.
- Keep `Current.bind(...)` active as long as the session lives. Use a new
  session for a different identity. The criteria do not remove objects that
  the session has already loaded, and `session.get()` returns such an object
  without a query.

A model that reaches its tenant through a relationship needs its own
criterion. The SaaS example has one for `Task` through its project, for
`TaskLabel` through its task and its label, and for `UploadLine` through its
upload. Write each criterion as a lambda. SQLAlchemy analyses a lambda once
and caches it. It processes a plain expression again for every statement,
also for statements that never read the model.

### What the listener does not cover

The listener filters ORM reads. It is not access control in the database.
Raw SQL, Core table queries and writes need their own checks. The criteria
reach a statement only through an entity that the statement selects or joins:

- `select(func.count()).where(Task.project_id == id)` names `Task` only in
  its `WHERE` clause, so it counts the tasks of every tenant. Add
  `.select_from(Task)`.
- A relationship condition built with `.any()` or `.has()` does not get the
  criteria inside its `EXISTS` subquery. Add the tenant condition there
  yourself, as the SaaS criteria do.

### Let an admin read every tenant

The listener checks `tenant_is_admin`, so an admin reads across tenants
through the same views. A flag that each request binds lets a rule skip its
filter. If the flag is not bound, the read fails with an error. This keeps one
set of routes, but every rule that an admin may skip must check the flag. For the rules in a scope, the other choice is a second admin view with
[its own scope](#view-scope). That needs more classes, but each view shows in
its class what it can read. A second view does not change the tenant rule,
because the rule lives on the session.

(soft-delete-mixin)=

## Soft-delete rows

A soft delete marks a row as deleted and keeps it in the table. It has three
parts:

- The model has a `deleted_at` column.
- The model's [default scope](#default-scope) hides deleted rows from every
  read and every reference check.
- A view mixin overrides `delete`, so `DELETE` sets `deleted_at` instead of
  removing the row.

The column comes from a model mixin:

```python
from datetime import datetime

from sqlalchemy import orm


class SoftDeletable(orm.MappedAsDataclass, kw_only=True):
    deleted_at: orm.Mapped[datetime | None] = orm.mapped_column(default=None)
```

(soft-delete-recipe)=

### Make `DELETE` set a timestamp

Override the {meth}`delete <fastapi_restly.views.RestView.delete>` business
method. It receives the loaded row. Set the timestamp, flush, and do not call
`super()`, because that would remove the row:

```python
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession


class SoftDeleteMixin:
    """``delete`` sets ``deleted_at`` instead of removing the row."""

    if TYPE_CHECKING:
        session: AsyncSession

    async def delete(self, obj: Any) -> None:
        obj.deleted_at = datetime.now(timezone.utc)
        await self.session.flush()
```

If only one view needs it, put the same `delete` method on that view. The
mixin has no `hasattr` check and no fallback to `super()`. It goes on views of
`SoftDeletable` models, so the column is always there.

{meth}`handle_delete <fastapi_restly.views.RestView.handle_delete>` still
loads the row through the scope, calls `authorize` and commits. The mixin only
changes what "delete" does. A query parameter can never widen a scope, so a
client cannot ask for deleted rows. To list the deleted rows or restore one,
add a route that [reads other rows](#per-read-scope).

(composing-on-a-view)=

### Put the parts together

The model declares its columns, and the model's clause namespace declares the
read rule. A view adds only the `delete` mixin:

```python
class Project(TenantOwned, AuditStamped, SoftDeletable, fr.TimestampsMixin, fr.IDMixin, Base):
    name: orm.Mapped[str]


class ProjectClauses(fr.ClauseNamespace):
    model = Project

    is_deleted = fr.where_clause(Project.deleted_at.is_not(None))
    default_scope = fr.none_of(is_deleted)  # the session has the tenant rule


@fr.include_view(app)
class ProjectView(SoftDeleteMixin, fr.AsyncRestView):
    prefix = "/projects"
    model = Project
    schema = ProjectSchema
    # no scope declared: reads apply ProjectClauses.default_scope
```

{meth}`get_many <fastapi_restly.views.RestView.get_many>`,
{meth}`count <fastapi_restly.views.RestView.count>` and
{meth}`get_one <fastapi_restly.views.RestView.get_one>` apply the default
scope. So lists, totals and single reads hide deleted rows. Updates and
deletes load the row through `get_one`, so they return 404 for a deleted row.
The session listener adds the tenant rule to these reads and to every
reference check. The columns stamp themselves on every write. The SaaS example
runs this view in
[`app/projects/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/projects/views.py).

## See also

- [Current & ContextParam](howto_current.md): bind the values that column
  defaults and the tenant listener read.
- [Scopes](scopes.md): hide rows from every read, and read past the scope on
  one route.
- [Request Lifecycle & Hooks](#business-methods): values that only the HTTP
  API sets, in `create` and `update`.
