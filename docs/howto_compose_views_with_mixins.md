# Compose Views with Mixins

Model mixins declare shared columns, such as tenant ids and audit stamps.
SQLAlchemy session listeners restrict tenant reads. Clause namespaces define
[scopes](scopes.md), such as hiding soft-deleted rows. View mixins override
shared behavior, such as setting a timestamp instead of deleting a row.

## Where structural concerns live

- **Tenant filters** belong in SQLAlchemy session listeners. They apply
  independently of the scope selected by a view or reference check.
- **Read scopes** are clauses. The model namespace's
  [default scope](#default-scope) covers RestView reads and reference
  check, and a view {attr}`scope <fastapi_restly.views.BaseRestView.scope>`
  replaces it for that view. Clauses compose with
  {func}`fr.all_of <fastapi_restly.clauses.all_of>`.
- **Server-stamped fields** are column defaults on the model, reading a
  `fr.ContextNamespace` slot when the row is written. A default fires on every
  write path: the view verbs, a custom route that builds the object by hand,
  the free `fr.objects` helpers, a bulk route. Nothing on the view has to run.
- **Soft delete** is a verb, so it is a view mixin overriding
  {meth}`delete <fastapi_restly.views.RestView.delete>`.

Keep per-view logic in the business method: hash passwords, derive slugs,
update rollups, and dispatch resource-specific events in
{meth}`create <fastapi_restly.views.RestView.create>` /
{meth}`update <fastapi_restly.views.RestView.update>`, as described in
[Override the business methods](customize.md#override-the-business-methods).
The discriminating question is whether the value depends on the request
payload. A field that only reads request context (the auth user id, the tenant
id) is structural and belongs on the model. A value computed from schema
fields, as in `hash_password(schema.password)` or
`slugify(schema.name) + uniqueness_probe`, belongs in a per-view `create` /
`update` override.

Why not stamp from a view mixin? `create` and `update` are overridden from
scratch: a `create` that hashes a password does not call `super()`, so a
stamp hooked on the verb is silently skipped by every view that overrides it.
The object utilities under the verbs (`make_new_object`, `update_object`,
`save_object`) are not override points either, and a stamp there would still
miss a hand-built object and the free helpers. The column covers all of them.

### Reusing logic outside the view

A per-view {meth}`create <fastapi_restly.views.RestView.create>` /
{meth}`update <fastapi_restly.views.RestView.update>` override has
`self.session`, `self.request`, and any mixin-provided state. That is usually
the right home for the logic.

If the same logic must also run from a script or worker, extract a plain
function, put it where it is easiest to find, and call it from both:

```python
def hash_and_set_password(user: User, raw_password: str) -> None:
    user.password = bcrypt.hashpw(raw_password.encode(), bcrypt.gensalt())


class UserView(fr.AsyncRestView):
    model = User
    schema = UserRead

    async def create(self, schema_obj):
        user = await self.make_new_object(schema_obj)
        hash_and_set_password(user, schema_obj.password)
        return await self.save_object(user)
```

`create` is commit-free, so the surrounding commit bracket persists the
password hash with the row. The view methods `make_new_object` and `save_object` wrap the free
functions in `fastapi_restly.objects`; import the free functions for workers
with a bare session.

Do not extract early. Wait until there is a second caller.

## Three structural pieces

The [SaaS example](examples.md#saas) ships the model mixins in
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


class TenantOwned(orm.MappedAsDataclass, kw_only=True):
    """The tenant column: the organization the request acts in."""

    organization_id: orm.Mapped[int] = orm.mapped_column(
        ForeignKey("organization.id"),
        init=False,
        insert_default=lambda: Current.org_id(),
    )


# Read identity when the SQL executes, not when the option is attached.
tenant_org_id = sa.bindparam(
    "tenant_org_id", callable_=lambda: Current.org_id(), type_=sa.Integer
)
tenant_is_admin = sa.bindparam(
    "tenant_is_admin", callable_=lambda: Current.is_admin(), type_=sa.Boolean
)


@sa.event.listens_for(orm.Session, "do_orm_execute")
def _restrict_tenant_rows(state: orm.ORMExecuteState) -> None:
    if not state.is_select or state.is_column_load:
        return
    state.statement = state.statement.options(
        orm.with_loader_criteria(
            TenantOwned,
            lambda cls: sa.or_(tenant_is_admin, cls.organization_id == tenant_org_id),
            include_aliases=True,
        )
    )


class Project(TenantOwned, fr.TimestampsMixin, fr.IDBase):
    name: orm.Mapped[str]
```

The column is the write half. The tenant is not a constructor argument and
not a payload field: `init=False` keeps it out of the constructor,
`fr.ReadOnly` on the schema keeps it out of the body, and the insert
default stamps it at flush from the same slot the listener reads. A row
lands in the organization the request acts in, whoever builds it; an admin
writing into another tenant acts as that tenant, and the stamp follows. A
verb that needs the value before the flush reads `Current.org_id()`
itself, as the example's slug probe does.

Attach the option even when the query starts from an unrestricted model:
`select(Organization).options(joinedload(Organization.users))` must filter
the users. Relationship queries also receive the option, including when
their parent was inserted rather than loaded by a query. Keep the default
`propagate_to_loaders=True` so joined eager loads receive the criteria.

[`bindparam(callable_=...)`](https://docs.sqlalchemy.org/en/20/core/sqlelement.html#sqlalchemy.sql.expression.bindparam)
reads the identity only when the generated SQL uses that parameter. A plain
lookup query needs no identity. A protected read without one fails with a
context `LookupError`, wrapped in SQLAlchemy's `StatementError`. The lambdas
are needed here because `bindparam` tests the callable's truth value, which
`ContextParam` rejects.

Keep `Current.bind(...)` active for the session's lifetime. Use a new session
for a different identity: criteria do not remove objects already loaded into
the session. A model that reaches its tenant through a relationship needs
its own criterion. The SaaS example covers `Task` through its project and
`TaskLabel` through both its task and label.

This is an ORM read filter, not database-level access control. Raw SQL, Core
table queries, and arbitrary writes need their own checks. Relationship
predicates built with `.any()` or `.has()` do not automatically receive the
criteria inside their `EXISTS` subquery. Include the tenant predicate there,
as the SaaS task and task-label listeners do.

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
[Restore a soft-deleted row](patterns.md#restore-a-soft-deleted-row).

### Audit stamps: two column defaults

`created_by_id` and `updated_by_id` are the plain case, a default per column:

```python
class AuditStamped(orm.MappedAsDataclass, kw_only=True):
    created_by_id: orm.Mapped[int | None] = orm.mapped_column(
        ForeignKey("user.id"), init=False, insert_default=lambda: Current.user_id()
    )
    updated_by_id: orm.Mapped[int | None] = orm.mapped_column(
        ForeignKey("user.id"),
        init=False,
        insert_default=lambda: Current.user_id(),
        onupdate=lambda: Current.user_id(),
    )
```

On a dataclass base `default` is the constructor default, so the insert-time
callable goes in `insert_default`; `onupdate` fires on every UPDATE that
changes the row. Keep these fields `fr.ReadOnly` on the schema: with
`init=False` a body value would otherwise reach the constructor and fail
loudly there.

The lambdas call the slot: a `ContextParam` resolves when called, and an
unbound context raises rather than stamping `None`. The columns stay
nullable for one row: the first user has no creator and comes from a
migration, which writes through its own table objects and fires no ORM
default. A worker or a seed script binds context around its writes:

```python
with Current.bind(org_id=7, user_id=1, is_admin=False):
    session.add(Project(name="Imported"))
    await session.flush()
```

## Composing on a view

The model declares what it is, the namespace declares the read half
against it, and a view stacks only the verb mixin:

```python
class Project(TenantOwned, AuditStamped, SoftDeletable, fr.TimestampsMixin, fr.IDBase):
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
    schema = ProjectRead
    # no scope declared: reads apply ProjectClauses.default_scope
```

{meth}`get_many <fastapi_restly.views.RestView.get_many>`,
{meth}`count <fastapi_restly.views.RestView.count>`, and
{meth}`get_one <fastapi_restly.views.RestView.get_one>` all apply the
default scope. The session listener also applies the tenant predicate to those
reads and to every reference check. Together they cover listings, totals,
single-row reads, updates, and deletes. The columns stamp themselves on every
write.

## Two ergonomic gotchas

Two recurring issues come up when writing mixins of this kind.

### 1. Type stubs on mixins must use `if TYPE_CHECKING:`

A mixin often needs to declare what it requires from its host class
(`session`, `request`, helper methods like `_current_org_id`). Declaring
those as plain class members shadows the host's implementation via the MRO:

```python
# WRONG: this real method body shadows the host's _current_org_id.
class SoftDeleteMixin:
    def _current_org_id(self) -> int | None:
        ...  # a stub body is still a real method
```

Wrap the stubs in `if TYPE_CHECKING:` so pyright sees them but Python
does not add them to the runtime class:

```python
class SoftDeleteMixin:
    if TYPE_CHECKING:
        def _current_org_id(self) -> int | None: ...
```

The same applies to typed *attribute* annotations. Marker-based DI (see
[Views](class_based_views.md#dependency-injection-on-class-attributes))
means a plain `model: type[DeclarativeBase]` annotation no longer
shadows DI wiring, but it can still shadow inherited attribute lookups
in some setups. `if TYPE_CHECKING:` is the safe wrapper for both.

### 2. Multiple FK columns to the same table need explicit `foreign_keys=`

`AuditStamped` adds `created_by_id` and `updated_by_id` columns,
both pointing at `User`. If the model already has another FK to `User`
(say, `assignee_id` on `Task`), SQLAlchemy cannot disambiguate the
existing relationship and raises `AmbiguousForeignKeysError`. Pin the
relationship explicitly:

```python
class Task(AuditStamped, fr.TimestampsMixin, fr.IDBase):
    assignee_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
    assignee: Mapped[User] = relationship(foreign_keys="Task.assignee_id")

    # Audit columns from AuditStamped add two more FKs to user.id.
    # Without foreign_keys="...", the assignee relationship is ambiguous.
```

This is the cost of opting into audit stamping on an already-related
model. Document it locally so the next reader does not have to rediscover
it.

## Admin bypass: runtime flag, not a parallel view tree

The ``is_admin`` conditional in the tenant listener points at a broader
decision. Admin endpoints often do not need a parallel view hierarchy; a
bound per-request flag lets a rule skip its filter, and a missing binding
fails loudly. This keeps the route tree simple, but every rule that should
widen for an admin must consult the flag. A parallel admin view tree (a
second view with its own ``scope``) gives class-time guarantees at the
cost of more classes.

Read scope is *visibility*, not *policy*. Rows outside the
[scope](scopes.md) return 404;
allow/deny decisions such as "only managers may create" belong in
{meth}`authorize <fastapi_restly.views.RestView.authorize>`, described in
[`authorize`: gate the action](customize.md#authorize-gate-the-action).

## Cross-references

- [Customizing RestView](customize.md): the three tiers,
  single-base overrides, and the call chain.
- [Views](class_based_views.md#dependency-injection-on-class-attributes):
  the marker-based DI rule that makes mixin type stubs safe.
- [Share Behaviour with Base Views](howto_inheritance.md): single-base
  shared logic, the simpler cousin to mixin composition.
