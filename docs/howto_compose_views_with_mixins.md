# Compose Views with Mixins

Some concerns belong on many views: tenant scoping, soft delete, audit stamps,
permission filters. They are structural, not business logic: they stamp
server-controlled fields or filter reads. Both halves of a structural field
hang off the model that declares the column. The read filter is a
[scope](scopes.md) clause in the model's namespace, and the write stamp is the
column's default. A view mixin carries the one structural concern that is a
verb, soft delete. This guide covers where each piece goes, the three pieces
the SaaS example ships, and two gotchas.

## Where structural concerns live

- **Read filters** are clauses. The model namespace's
  [default scope](#default-scope) covers every read and every reference
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
    user.password_hash = bcrypt.hashpw(raw_password.encode(), bcrypt.gensalt())


class UserView(fr.AsyncRestView):
    model = User
    schema = UserRead

    async def create(self, schema_obj):
        user = await self.make_new_object(schema_obj)
        hash_and_set_password(user, schema_obj.password)
        return await self.save_object(user)
```

`create` is commit-free, so the handler commits the password hash with the
row. The view methods `make_new_object` and `save_object` wrap the free
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
### Tenant row scoping: a scope clause plus a stamped column

The read half is a clause factory: one function builds the tenant predicate
for any model with an ``organization_id`` column. A called slot returns
its bound value, so the clause branches on values a generated dependency
(``Current.depends(...)``, see the SaaS example) binds once per request:

```python
from typing import Any
import sqlalchemy as sa
import fastapi_restly as fr


class Current(fr.ContextNamespace):
    org_id: fr.ContextParam[int | None]
    user_id: fr.ContextParam[int | None]
    is_admin: fr.ContextParam[bool]


def tenant_scope(model: type[Any]) -> fr.WhereClause:
    """Rows of ``model`` owned by the authenticated organization."""

    @fr.where_clause
    def owned_by_tenant() -> sa.ColumnElement[bool]:
        # Read before the admin check: an unbound context stays a loud error.
        org_id = Current.org_id()
        if Current.is_admin() or org_id is None:
            return sa.true()
        return model.organization_id == org_id

    return owned_by_tenant
```

The write half is the column itself, on a model mixin. The tenant is stamped
at construction rather than at flush, through an `init` listener, so a verb
that reads it before saving (a slug probe scoped to the organization) already
sees the stamped value:

```python
from sqlalchemy import ForeignKey, orm


class TenantOwned(orm.MappedAsDataclass, kw_only=True):
    """The tenant column, stamped from context at construction."""

    organization_id: orm.Mapped[int] = orm.mapped_column(
        ForeignKey("organization.id")
    )


@sa.event.listens_for(TenantOwned, "init", propagate=True)
def _stamp_tenant(target, args, kwargs) -> None:
    org_id = Current.org_id()
    if org_id is not None:
        kwargs["organization_id"] = org_id


class Project(TenantOwned, fr.TimestampsMixin, fr.IDBase):
    name: orm.Mapped[str]
```

With an organization in context the stamp wins over whatever the payload
said; without one, the caller's value stands, which is the admin path in the
example. A namespace base can derive the read clause from the same column,
so a model declares its tenancy once (see `TenantClauses` in the SaaS
example's `context.py`).

(soft-delete-mixin)=
### Soft delete: a scope clause plus a delete mixin

The read half is one unconditional predicate in the model's namespace:
the default scope hides deleted rows for every read and every reference
check, and the trash is reachable only through an explicit surface, a
view declaring `scope = ProjectClauses.trashed` (see
[Scopes](#view-scope)). A query parameter can never widen a scope. The
write half turns `delete` into a timestamp flip:

```python
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from sqlalchemy.ext.asyncio import AsyncSession


class SoftDeletable(orm.MappedAsDataclass, kw_only=True):
    deleted_at: orm.Mapped[datetime | None] = orm.mapped_column(default=None)


class ProjectClauses(fr.ClauseNamespace):
    model = Project

    owned_by_tenant = tenant_scope(Project)
    is_deleted = fr.where_clause(Project.deleted_at.is_not(None))
    trashed = fr.all_of(owned_by_tenant, is_deleted)
    default_scope = fr.all_of(owned_by_tenant, fr.none_of(is_deleted))


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
        ForeignKey("user.id"), default=None, insert_default=lambda: Current.user_id()
    )
    updated_by_id: orm.Mapped[int | None] = orm.mapped_column(
        ForeignKey("user.id"),
        default=None,
        insert_default=lambda: Current.user_id(),
        onupdate=lambda: Current.user_id(),
    )
```

On a dataclass base `default` is the constructor default, so the insert-time
callable goes in `insert_default`; `onupdate` fires on every UPDATE that
changes the row. A payload value wins over a default, so keep these fields
`fr.ReadOnly` on the schema.

The lambdas call the slot: a `ContextParam` resolves when called, and an
unbound context raises rather than stamping `None`. A worker or a seed script
binds context around its writes:

```python
with Current.bind(org_id=7, user_id=1, is_admin=False):
    session.add(Project(name="Imported"))
    await session.flush()
```

## Composing on a view

The model declares what it is, and both halves follow. A view stacks only
the verb mixin:

```python
class Project(TenantOwned, AuditStamped, SoftDeletable, fr.TimestampsMixin, fr.IDBase):
    name: orm.Mapped[str]


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
default scope, so tenant and soft-delete filters cover listings, totals,
single-row reads, updates, and deletes, and every reference to Project
checks it too; the columns stamp themselves on every write.

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

The ``admin`` branch in ``tenant_scope`` points at a broader decision. Admin
endpoints often do not need a parallel view hierarchy; a bound per-request
flag lets each scope clause skip its filter, and a missing binding fails
loudly. This keeps the route tree simple, but every scope clause must
consult the flag. A parallel admin view tree (a second view with its own
``scope``) gives class-time guarantees at the cost of more classes.

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
