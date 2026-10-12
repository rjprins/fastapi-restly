# Models

Restly works with SQLAlchemy models on a declarative base of your own. The
base is your class, with its own registry and `MetaData`, as in any
SQLAlchemy project. Restly does not own it. It gives you two kinds of help:

- {class}`fr.ModelDefaults <fastapi_restly.models.ModelDefaults>` adds
  Restly's defaults to your base.
- {class}`fr.IDMixin <fastapi_restly.models.IDMixin>` and
  {class}`fr.TimestampsMixin <fastapi_restly.models.TimestampsMixin>` add
  common columns to a model.

All of it is optional. Restly's views also work with plain SQLAlchemy models.

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
  [Relationship Loading and Async](#relationship-loading) shows.

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

## Use the base in your app

Everything that asks for your models' tables takes the base:

- {func}`fr.db.async_create_all(Base) <fastapi_restly.db.async_create_all>`
  creates the tables during development.
- `fr.testing.configure_tests(base=Base)` builds and cleans the test tables,
  as [Testing](howto_testing.md) shows.
- Alembic's `env.py` sets `target_metadata = Base.metadata`, as
  [Deploying](deploying.md) shows.

A base only knows the models that have been imported.
[New & Existing Projects](howto_project_structure.md) shows how one module imports
them all.

## Models without Restly's defaults

Restly's views and schemas work with any SQLAlchemy declarative model,
dataclass or not. Models that you already have can stay on their own base, as
[Use Your Existing Models](#existing-models) shows.

Adding `fr.ModelDefaults` to a base that already has models changes those
models:

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

(existing-models)=

## Use Your Existing Models

If your project already has SQLAlchemy models on its own `DeclarativeBase`,
you can use those models directly in a
{class}`RestView <fastapi_restly.views.RestView>`:

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

{class}`RestView <fastapi_restly.views.RestView>` supports these models for
default CRUD routes and
[auto-generated schemas](#auto-generated-schemas). Pass
the same base wherever Restly asks for your models' tables, as in
`fr.db.async_create_all(AppBase)` and
`fr.testing.configure_tests(base=AppBase)`.

Restly's model defaults, such as dataclass constructors and datetimes with a
time zone, are optional. [Models](howto_models.md) explains them,
and what changes when you add them to a base that already has models.

## Where structural concerns live

- **Tenant filters** belong in SQLAlchemy session listeners. They apply
  independently of the scope selected by a view or reference check.
- **Read scopes** are clauses. The model namespace's
  [default scope](#default-scope) covers RestView reads and reference
  check, and a view {attr}`scope <fastapi_restly.views.BaseRestView.scope>`
  replaces it for that view. Clauses compose with
  {func}`fr.all_of <fastapi_restly.clauses.all_of>`.
- **Server-stamped fields** are column defaults on the model, reading a
  `fr.ContextNamespace` member when the row is written. A default fires on every
  write path: the view verbs, a custom route that builds the object by hand,
  the free `fr.objects` helpers, a bulk route. Nothing on the view has to run.
- **Soft delete** is a verb, so it is a view mixin overriding
  {meth}`delete <fastapi_restly.views.RestView.delete>`.

Why not stamp from a view mixin? `create` and `update` are overridden from
scratch: a `create` that hashes a password does not call `super()`, so a
stamp hooked on the verb is silently skipped by every view that overrides it.
The object utilities under the verbs (`make_new_object`, `update_object`,
`save_object`) are not override points either, and a stamp there would still
miss a hand-built object and the free helpers. The column covers all of them.

(server-stamped-fields)=

## Server-stamped fields: column defaults on the model

A field the server owns (an audit id, a tenant id) is a column default that reads a bound {class}`fr.ContextNamespace <fastapi_restly.clauses.ContextNamespace>` member. It fires on every write path, whichever view or helper built the row, so nothing on the view has to run:

```python
class Article(fr.TimestampsMixin, fr.IDMixin, Base):
    title: Mapped[str]
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id"), init=False, insert_default=Current.user_id
    )
```

`init=False` leaves the field out of the constructor. SQLAlchemy calls
`Current.user_id` when it inserts the row. Pass the member without
parentheses, as [Read the current user in a column default](#current-column-defaults)
explains.

Keep the field `fr.ReadOnly` on the schema and out of any [explicit write schema](#explicit-write-schemas). With `init=False`, a payload value for it would fail in the constructor. Stamping from the view instead, in `create`, only covers that verb: a `create` override does not call `super()`, and the free `fr.objects` helpers never see the view. See [Three structural pieces](#structural-pieces) for the tenant, audit, and soft-delete pieces the SaaS example ships.

A derivation that needs more than a value, a slug from the title or a denormalised counter, is a SQLAlchemy `before_insert` mapper event on the same model:

```python
from sqlalchemy import event

@event.listens_for(Article, "before_insert")
def _set_slug(mapper, connection, target):
    target.slug = slugify(target.title)
```

See SQLAlchemy's [mapper events documentation](https://docs.sqlalchemy.org/en/20/orm/events.html#mapper-events) for the full event API.

(structural-pieces)=

## Three structural pieces

The [SaaS example](https://github.com/rjprins/fastapi-restly/tree/main/example-projects/saas) ships the model mixins in
[`app/models.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/models.py)
and the view mixin in
[`app/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/views.py).
Copy them into your project as a starting point.

(tenant-row-scoping)=
### Tenant row scoping: a session rule plus a stamped column

The tenant rule is not a scope. It must hold under every scope a view or a
route can name, and for every reference check, so it lives at the session
level, where SQLAlchemy already provides it: a `do_orm_execute` listener
adds a
[`with_loader_criteria`](https://docs.sqlalchemy.org/en/20/orm/queryguide/api.html#sqlalchemy.orm.with_loader_criteria)
option to ORM entity and relationship reads. The values
it reads come from a generated dependency (`Current.depends(...)`, see the
SaaS example) that binds them once per request, and they are never `None`:
a request without an authenticated identity is refused by the dependency's
sources (401), and the admin is a conditional, not an absent value.

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

The column is the write half. The tenant is not a constructor argument and
not a payload field: `init=False` keeps it out of the constructor,
`fr.ReadOnly` on the schema keeps it out of the body, and the insert
default stamps it at flush from the same member the listener reads. A row
lands in the organization the request acts in, whoever builds it; an admin
writing into another tenant acts as that tenant, and the stamp follows. A
verb that needs the value before the flush reads `Current.org_id()`
itself, as the example's slug probe does.

Attach the option even when the query starts from an unrestricted model:
`select(Organization).options(joinedload(Organization.users))` must filter
the users. Relationship queries also receive the option, including when
their parent was inserted rather than loaded by a query. Keep the default
`propagate_to_loaders=True` so joined eager loads receive the criteria.

A relationship load inherits the options of the statement that loaded its
parent. Without the `TenantCriteria` marker the listener adds a second copy of
each criterion to that load, and a chain of loads, such as a task's subtasks,
gains one more copy at every level. The marker travels with the criteria, so
the listener skips a statement that has it.

[`bindparam(callable_=...)`](https://docs.sqlalchemy.org/en/20/core/sqlelement.html#sqlalchemy.sql.expression.bindparam)
reads the identity only when the generated SQL uses that parameter. A plain
lookup query needs no identity. A protected read without one fails with a
context `LookupError`, wrapped in SQLAlchemy's `StatementError`. The lambdas
are needed here because `bindparam` tests the callable's truth value, which
`ContextParam` rejects.

Keep `Current.bind(...)` active for the session's lifetime. Use a new session
for a different identity: criteria do not remove objects already loaded into
the session, and `session.get()` returns such an object without a query.

A model that reaches its tenant through a relationship needs its own
criterion. The SaaS example registers one for `Task` through its project, for
`TaskLabel` through both its task and its label, and for `UploadLine` through
its upload. Write each criterion as a lambda. SQLAlchemy analyses a lambda
once and caches it. It processes a plain expression again for every
statement, including statements that never read the model.

This is an ORM read filter, not database-level access control. Raw SQL, Core
table queries, and arbitrary writes need their own checks. The criteria reach
a statement only through an entity it selects or joins.
`select(func.count()).where(Task.project_id == id)` names `Task` only in its
`WHERE` clause, so it counts every tenant's tasks. Add `.select_from(Task)`.
Relationship predicates built with `.any()` or `.has()` do not automatically
receive the criteria inside their `EXISTS` subquery. Include the tenant
predicate there, as the SaaS criteria do.

#### Admin bypass: runtime flag, not a parallel view tree

The ``is_admin`` conditional in the tenant listener points at a broader
decision. Admin endpoints often do not need a parallel view hierarchy; a
bound per-request flag lets a rule skip its filter, and a missing binding
fails loudly. This keeps the route tree simple, but every rule that should
widen for an admin must consult the flag. A parallel admin view tree (a
second view with its own ``scope``) gives class-time guarantees at the
cost of more classes.

(soft-delete-mixin)=
### Soft delete: a scope clause plus a delete mixin

The read half is one unconditional predicate in the model's namespace,
`is_deleted`, declared with the composed model under
[Composing on a view](#composing-on-a-view): the default scope hides
deleted rows for every read and every reference check, and the trash is
reachable only through an explicit surface, a view declaring
`scope = ProjectClauses.trashed` (see [Scopes](#view-scope)). A query
parameter can never widen a scope. The write half turns `delete` into a
timestamp flip:

```python
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from sqlalchemy.ext.asyncio import AsyncSession


class SoftDeletable(orm.MappedAsDataclass, kw_only=True):
    deleted_at: orm.Mapped[datetime | None] = orm.mapped_column(default=None)


class SoftDeleteMixin:
    """``delete`` flips ``deleted_at`` instead of removing the row."""

    if TYPE_CHECKING:
        session: AsyncSession

    async def delete(self, obj: Any) -> None:
        obj.deleted_at = datetime.now(timezone.utc)
        await self.session.flush()
```

No `hasattr` guard and no fallback to `super()`: the mixin goes on views of
`SoftDeletable` models, and the column is there because the model said so.
The flip overrides the {meth}`delete <fastapi_restly.views.RestView.delete>`
business method, not the handler.
{meth}`handle_delete <fastapi_restly.views.RestView.handle_delete>` still
loads, authorizes, and commits; the mixin only changes what "delete" does. To
bring a flipped row back, see
[A route names its own scope](#per-read-scope).

(soft-delete-recipe)=

#### `delete`: soft-delete instead of removing the row

{meth}`delete <fastapi_restly.views.RestView.delete>` also receives the loaded object. Flip a timestamp instead of deleting:

```python
    async def delete(self, obj):
        obj.deleted_at = datetime.now(timezone.utc)
        await self.session.flush()
        # Do not call super(); that would remove the row.
```

For reusable soft-delete that also hides rows on read, see `SoftDeleteMixin` in [Soft delete](#soft-delete-mixin).

(composing-on-a-view)=

#### Composing on a view

The model declares what it is, the namespace declares the read half
against it, and a view stacks only the verb mixin:

```python
class Project(TenantOwned, AuditStamped, SoftDeletable, fr.TimestampsMixin, fr.IDMixin, Base):
    name: orm.Mapped[str]


class ProjectClauses(fr.ClauseNamespace):
    model = Project

    is_deleted = fr.where_clause(Project.deleted_at.is_not(None))
    default_scope = fr.none_of(is_deleted)  # the tenant rule is the session's
    trashed = is_deleted


@fr.include_view(app)
class ProjectView(SoftDeleteMixin, fr.AsyncRestView):
    prefix = "/projects"
    model = Project
    schema = ProjectSchema
    # no scope declared: reads apply ProjectClauses.default_scope
```

{meth}`get_many <fastapi_restly.views.RestView.get_many>`,
{meth}`count <fastapi_restly.views.RestView.count>`, and
{meth}`get_one <fastapi_restly.views.RestView.get_one>` all apply the
default scope. The session listener also applies the tenant predicate to those
reads and to every reference check. Together they cover lists, totals,
single-row reads, updates, and deletes. The columns stamp themselves on every
write.

### Audit stamps: two column defaults

`created_by_id` and `updated_by_id` are the plain case, a default per column:

```python
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
```

On a dataclass base `default` is the constructor default, so the insert-time
callable goes in `insert_default`; `onupdate` fires on every UPDATE that
changes the row. Keep these fields `fr.ReadOnly` on the schema: with
`init=False` a body value would otherwise reach the constructor and fail
loudly there.

SQLAlchemy calls the member when it writes the row, and an unbound member
raises an error rather than stamping `None`. The columns stay
nullable for one row: the first user has no creator and comes from a
migration, which writes through its own table objects and fires no ORM
default. A worker or a seed script binds context around its writes:

```python
with Current.bind(org_id=7, user_id=1, is_admin=False):
    session.add(Project(name="Imported"))
    await session.flush()
```

#### Multiple FK columns to the same table need explicit `foreign_keys=`

`AuditStamped` adds `created_by_id` and `updated_by_id` columns,
both pointing at `User`. If the model already has another FK to `User`
(say, `assignee_id` on `Task`), SQLAlchemy cannot disambiguate the
existing relationship and raises `AmbiguousForeignKeysError`. Pin the
relationship explicitly:

```python
class Task(AuditStamped, fr.TimestampsMixin, fr.IDMixin, Base):
    assignee_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
    assignee: Mapped[User] = relationship(foreign_keys="Task.assignee_id")

    # Audit columns from AuditStamped add two more FKs to user.id.
    # Without foreign_keys="...", the assignee relationship is ambiguous.
```

This is the cost of opting into audit stamping on an already-related
model. Document it locally so the next reader does not have to rediscover
it.
