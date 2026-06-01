# How-To: Compose Views with Mixins

Some concerns belong on many views: tenant scoping, soft delete, audit stamps,
permission filters. They are structural, not business logic: they stamp
server-controlled fields or add read filters. Python mixins layer these concerns
through cooperative `super()` calls.

This guide covers the pattern, when to use it, and two gotchas.

## The structural override points

Three [override points](howto_override_endpoints.md) carry almost all
structural concerns:

- `build_query` — the unified **read scope**. List, count, and retrieve
  all route through it, so one `.where(...)` clause filters every read.
- `make_new_object` / `update_object` — **cooperative field stamping**.
  Each calls `super()` to get the constructed object, mutates the
  server-controlled fields it owns, and returns the object. Mixins layer
  by chaining `super()`, each stamping its own fields on the way out.
- the `delete` business verb — to replace a physical delete with a flag
  flip (soft delete).

Do not use these for per-view application logic.

**Rule 1 — keep per-view logic in the business verb.** Hash passwords, derive
slugs, update rollups, and dispatch resource-specific events in `create` /
`update`. See [Override Endpoints](howto_override_endpoints.md).

**Rule 2 — use mixins for structural concerns.** Good examples: audit stamps,
tenant ids, soft-delete read filters, and soft-delete mutation. These compose
because:

- `make_new_object` / `update_object` mutate the object *after* the
  schema's own writes are applied, so they compose cleanly and never
  fight the schema's writes.
- They run inside the commit-free business verb, before the handler commits.
- They only stamp/scope; they don't compute business values from
  schema inputs.
- They compose linearly via cooperative `super()` calls, so combinations
  work without ordering surprises.

**The discriminator:** does the override depend on schema-derived business
inputs?

- If it only reads request context (auth user id, tenant id, request
  flags) and writes server-controlled fields → mixin (Rule 2).
- If it reads schema fields and computes values from them
  (`hash_password(schema.password)`, `slugify(schema.name) +
  uniqueness_probe`) → a per-view `create` / `update` override, written
  from scratch (Rule 1).

### Reusing logic outside the view

A per-view `create` / `update` override has `self.session`, `self.request`, and
any mixin-provided state. That is usually the right home for the logic.

If the same logic must also run from a script or worker, extract a plain
function and call it from both. Put it where it is easiest to find.

```python
from fastapi_restly.objects import async_make_new_object


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

`create` is commit-free, so the handler commits the password hash with the row.

`make_new_object` / `save_object` wrap the free functions in
`fastapi_restly.objects`. Import the free functions for workers with a bare
session.

Do not extract early. Wait until there is a second caller.

## Three reusable mixins

The
[SaaS example](https://github.com/rjprins/fastapi-restly/tree/main/example-projects/saas/app/views/_mixins.py)
ships three mixins demonstrating Rule 2. Copy them into your project as a
starting point.

### `TenantScopedMixin` — multi-tenant row scoping

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
        # Filters get_many, count, AND get_one via the framework's unified
        # read scope -- no separate retrieve override needed.
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

### `SoftDeleteMixin` — hide deleted rows

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

The soft-delete flip overrides the `delete` business verb, not the handler.
`handle_delete` still loads, authorizes, and commits. The mixin only changes
what "delete" does.

### `AuditStampedMixin` — record who created/updated each row

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
schema writes are already applied, so stamps do not collide with input fields.

## Composing mixins on a view

The mixins layer through cooperative `super()` calls. Order matters only
for short-circuit behaviour (e.g. `_is_admin()` skipping tenant
scoping). A typical project view:

```python
@fr.include_view(app)
class ProjectView(SoftDeleteMixin, AuditStampedMixin, TenantScopedMixin, fr.AsyncRestView):
    prefix = "/projects"
    model = Project
    schema = ProjectRead
```

`get_many`, `count`, and `get_one` all use `build_query`, so tenant and
soft-delete filters apply to listings, totals, single-row reads, updates, and
deletes.

## Two ergonomic gotchas

### 1. Type stubs on mixins must use `if TYPE_CHECKING:`

A mixin often needs to declare what it requires from its host class
(`session`, `request`, helper methods like `_current_org_id`). Declaring
those as plain class members shadows the host's implementation via MRO:

```python
# WRONG — this real method body shadows the host's _current_org_id.
class TenantScopedMixin:
    def _current_org_id(self) -> int | None:
        ...  # stub body — but stub bodies are still real methods
```

Wrap the stubs in `if TYPE_CHECKING:` so pyright sees them but Python
doesn't add them to the runtime class:

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
(say, `assignee_id` on `Task`), SQLAlchemy can't disambiguate the
existing relationship and raises `AmbiguousForeignKeysError`. Pin it:

```python
class Task(fr.TimestampsMixin, fr.IDBase):
    assignee_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
    assignee: Mapped[User] = relationship(foreign_keys="Task.assignee_id")

    # Audit columns from AuditStampedMixin add two more FKs to user.id.
    # Without foreign_keys="...", the assignee relationship is ambiguous.
```

This is the cost of opting into audit-stamping on an already-related
model. Document it locally so the next reader doesn't have to rediscover
it.

## Admin bypass — runtime flag, not a parallel view tree

Admin endpoints often do not need a parallel view hierarchy. A per-request
`_is_admin()` predicate can let each scope-filtering mixin skip its filter. This
keeps the route tree simple, but every read-scope mixin must consult the flag.
A parallel admin view tree gives class-time guarantees at the cost of more
classes.

Read scope is *visibility*, not *policy*. Rows hidden by `build_query` return
404; allow/deny decisions such as "only managers may create" belong in
`authorize`.

## Cross-references

- [Override Endpoints](howto_override_endpoints.md) — the three tiers,
  single-base overrides, and the call chain.
- [Class-Based Views](class_based_views.md#dependency-injection-on-class-attributes)
  — the marker-based DI rule that makes mixin type stubs safe.
- [Share Behaviour with Base Views](howto_inheritance.md) — single-base
  shared logic, the simpler cousin to mixin composition.
