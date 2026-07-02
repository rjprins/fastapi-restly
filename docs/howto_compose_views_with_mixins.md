# Compose Views with Mixins

Some concerns belong on many views: tenant scoping, soft delete, audit stamps,
permission filters. They are structural, not business logic: they stamp
server-controlled fields or add read filters. Python mixins layer these concerns
through cooperative `super()` calls. This guide covers the pattern, when to use
it, and two gotchas.

## The structural override points

Three [override points](customize.md) carry almost all
structural concerns:

- {meth}`build_query <fastapi_restly.views.RestView.build_query>` is the
  unified [read scope](customize.md#build_query-scope-every-read-at-once).
  List, count, and retrieve all route through it, so one `.where(...)` clause
  filters every read.
- `make_new_object` and `update_object` perform
  [cooperative field stamping](customize.md#cooperative-field-stamping-override-make_new_object--update_object).
  Each calls `super()` to get the constructed object, mutates the
  server-controlled fields it owns, and returns the object. Mixins layer
  by chaining `super()`, each stamping its own fields on the way out.
- The {meth}`delete <fastapi_restly.views.RestView.delete>` business verb is
  overridden to replace a physical delete with a flag flip (soft delete).

Do not use these for per-view application logic. Keep per-view logic in the
business verb: hash passwords, derive slugs, update rollups, and dispatch
resource-specific events in {meth}`create <fastapi_restly.views.RestView.create>` /
{meth}`update <fastapi_restly.views.RestView.update>`, as described in
[Override the business methods](customize.md#override-the-business-methods).

Use mixins for the structural concerns instead: audit stamps, tenant ids,
soft-delete read filters, and soft-delete mutation are all good examples.
These compose because:

- `make_new_object` and `update_object` mutate the object *after* the
  schema's own writes are applied, so they compose cleanly and never
  fight the schema's writes.
- They run inside the commit-free business verb, before the handler commits.
- They only stamp and scope; they do not compute business values from
  schema inputs.
- They compose linearly via cooperative `super()` calls, so combinations
  work without ordering surprises.

The discriminating question is whether the override depends on schema-derived
business inputs. If it only reads request context (the auth user id, tenant
id, or request flags) and writes server-controlled fields, it is structural
and belongs in a mixin. If it reads schema fields and computes values from
them, as in `hash_password(schema.password)` or
`slugify(schema.name) + uniqueness_probe`, write a per-view `create` /
`update` override from scratch.

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

## Three reusable mixins

The [SaaS example](examples.md#saas) ships three structural mixins of this
kind in
[`_mixins.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/views/_mixins.py).
Copy them into your project as a starting point.

### `TenantScopedMixin`: multi-tenant row scoping

`TenantScopedMixin` stamps `organization_id` from the authenticated request on
writes and filters every read to the same organization:

```python
import fastapi
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase
from typing import TYPE_CHECKING, Any
import sqlalchemy as sa


class TenantScopedMixin:
    """Stamp ``organization_id`` from auth on writes; filter reads to it."""

    if TYPE_CHECKING:
        request: fastapi.Request
        session: AsyncSession
        model: type[DeclarativeBase]
        def _current_org_id(self) -> int | None: ...
        def _is_admin(self) -> bool: ...

    def build_query(self) -> sa.Select:
        # Filters get_many, count, AND get_one via the framework's
        # unified read scope; no separate retrieve override is needed.
        q = super().build_query()  # type: ignore[misc]
        if self._is_admin():
            return q
        org_id = self._current_org_id()
        if org_id is not None and hasattr(self.model, "organization_id"):
            q = q.where(self.model.organization_id == org_id)
        return q

    async def make_new_object(self, schema_obj: Any) -> Any:
        obj = await super().make_new_object(schema_obj)  # type: ignore[misc]
        org_id = self._current_org_id()
        if org_id is not None and hasattr(self.model, "organization_id"):
            obj.organization_id = org_id
        return obj
```

The `if TYPE_CHECKING:` block declares what the mixin expects from its host
class (see the gotchas below).

### `SoftDeleteMixin`: hide deleted rows

`SoftDeleteMixin` filters deleted rows out of every read unless the request
asks for them, and turns `delete` into a timestamp flip:

```python
from datetime import datetime, timezone


class SoftDeleteMixin:
    """Hide deleted rows; ``delete`` flips ``deleted_at`` instead."""

    if TYPE_CHECKING:
        request: fastapi.Request
        session: AsyncSession
        model: type[DeclarativeBase]
        def save_object(self, obj: Any) -> Any: ...

    def _include_deleted(self) -> bool:
        return self.request.query_params.get("include_deleted", "false").lower() == "true"

    def build_query(self) -> sa.Select:
        q = super().build_query()  # type: ignore[misc]
        if not self._include_deleted() and hasattr(self.model, "deleted_at"):
            q = q.where(self.model.deleted_at.is_(None))
        return q

    async def delete(self, obj: Any) -> None:
        if hasattr(obj, "deleted_at"):
            obj.deleted_at = datetime.now(timezone.utc)
            await self.save_object(obj)
            return
        await super().delete(obj)  # type: ignore[misc]
```

The soft-delete flip overrides the {meth}`delete <fastapi_restly.views.RestView.delete>`
business verb, not the handler.
{meth}`handle_delete <fastapi_restly.views.RestView.handle_delete>` still
loads, authorizes, and commits; the mixin only changes what "delete" does. To
bring a flipped row back, see
[Restore a soft-deleted row](patterns.md#restore-a-soft-deleted-row).

### `AuditStampedMixin`: record who created/updated each row

`AuditStampedMixin` stamps `created_by_id` and `updated_by_id` from request
state on every write:

```python
class AuditStampedMixin:
    """Stamp ``created_by_id`` / ``updated_by_id`` from request state."""

    if TYPE_CHECKING:
        request: fastapi.Request

    def _current_user_id(self) -> int | None:
        return getattr(self.request.state, "user_id", None)

    async def make_new_object(self, schema_obj: Any) -> Any:
        obj = await super().make_new_object(schema_obj)  # type: ignore[misc]
        uid = self._current_user_id()
        obj.created_by_id = uid
        obj.updated_by_id = uid
        return obj

    async def update_object(self, obj: Any, schema_obj: Any) -> Any:
        obj = await super().update_object(obj, schema_obj)  # type: ignore[misc]
        obj.updated_by_id = self._current_user_id()
        return obj
```

Each mixin calls `super()`, stamps its fields, and returns the object. By then
the schema's writes are already applied, so stamps do not collide with input
fields.

## Composing mixins on a view

The mixins layer through cooperative `super()` calls, and order matters only
for short-circuit behaviour (for example `_is_admin()` skipping tenant
scoping). A typical project view composes all three:

```python
@fr.include_view(app)
class ProjectView(SoftDeleteMixin, AuditStampedMixin, TenantScopedMixin, fr.AsyncRestView):
    prefix = "/projects"
    model = Project
    schema = ProjectRead
```

{meth}`get_many <fastapi_restly.views.RestView.get_many>`,
{meth}`count <fastapi_restly.views.RestView.count>`, and
{meth}`get_one <fastapi_restly.views.RestView.get_one>` all use
{meth}`build_query <fastapi_restly.views.RestView.build_query>`, so tenant and
soft-delete filters apply to listings, totals, single-row reads, updates, and
deletes.

## Two ergonomic gotchas

Two recurring issues come up when writing mixins of this kind.

### 1. Type stubs on mixins must use `if TYPE_CHECKING:`

A mixin often needs to declare what it requires from its host class
(`session`, `request`, helper methods like `_current_org_id`). Declaring
those as plain class members shadows the host's implementation via the MRO:

```python
# WRONG: this real method body shadows the host's _current_org_id.
class TenantScopedMixin:
    def _current_org_id(self) -> int | None:
        ...  # a stub body is still a real method
```

Wrap the stubs in `if TYPE_CHECKING:` so pyright sees them but Python
does not add them to the runtime class:

```python
class TenantScopedMixin:
    if TYPE_CHECKING:
        def _current_org_id(self) -> int | None: ...
```

The same applies to typed *attribute* annotations. Marker-based DI (see
[Class-Based Views](class_based_views.md#dependency-injection-on-class-attributes))
means a plain `model: type[DeclarativeBase]` annotation no longer
shadows DI wiring, but it can still shadow inherited attribute lookups
in some setups. `if TYPE_CHECKING:` is the safe wrapper for both.

### 2. Multiple FK columns to the same table need explicit `foreign_keys=`

`AuditStampedMixin` adds `created_by_id` and `updated_by_id` columns,
both pointing at `User`. If the model already has another FK to `User`
(say, `assignee_id` on `Task`), SQLAlchemy cannot disambiguate the
existing relationship and raises `AmbiguousForeignKeysError`. Pin the
relationship explicitly:

```python
class Task(fr.TimestampsMixin, fr.IDBase):
    assignee_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
    assignee: Mapped[User] = relationship(foreign_keys="Task.assignee_id")

    # Audit columns from AuditStampedMixin add two more FKs to user.id.
    # Without foreign_keys="...", the assignee relationship is ambiguous.
```

This is the cost of opting into audit stamping on an already-related
model. Document it locally so the next reader does not have to rediscover
it.

## Admin bypass: runtime flag, not a parallel view tree

The `_is_admin()` check in `TenantScopedMixin` points at a broader decision.
Admin endpoints often do not need a parallel view hierarchy; a per-request
`_is_admin()` predicate can let
each scope-filtering mixin skip its filter. This keeps the route tree simple,
but every read-scope mixin must consult the flag. A parallel admin view tree
gives class-time guarantees at the cost of more classes.

Read scope is *visibility*, not *policy*. Rows hidden by
{meth}`build_query <fastapi_restly.views.RestView.build_query>` return 404;
allow/deny decisions such as "only managers may create" belong in
{meth}`authorize <fastapi_restly.views.RestView.authorize>`, described in
[`authorize`: gate the action](customize.md#authorize-gate-the-action).

## Cross-references

- [Customize RestView](customize.md): the three tiers,
  single-base overrides, and the call chain.
- [Class-Based Views](class_based_views.md#dependency-injection-on-class-attributes):
  the marker-based DI rule that makes mixin type stubs safe.
- [Share Behaviour with Base Views](howto_inheritance.md): single-base
  shared logic, the simpler cousin to mixin composition.
