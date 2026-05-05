# How-To: Compose Views with Mixins

Some concerns belong on every view: tenant scoping, soft delete, audit
stamping, permission filtering. They aren't *business* logic — they're
*structural*: they don't compute values from the schema's inputs, they
just stamp server-controlled fields on writes and add `WHERE` clauses
on reads. Layered through Python mixins, they compose linearly via
cooperative `super()` calls and reduce per-view boilerplate to a single
mixin declaration.

This guide covers the pattern, the rule that decides whether to use it,
and two ergonomic gotchas worth knowing up front.

## When to override `make_new_object` / `update_object` / `delete_object` / `build_query`

The [Override Endpoints](howto_override_endpoints.md#override-low-level-object-helpers)
guide warns against overriding these low-level helpers for per-view
business logic — password hashing, slug derivation, denormalised rollups,
status-transition events. Those belong in `perform_create` / `perform_update`,
written from scratch using the [`make_new_object` /
`save_object`](api_reference.md#crud-utility-free-functions) helpers.

There is one carve-out where overriding these helpers is the *right*
answer:

**Rule 1 — don't override these helpers for per-view application logic.**
Hashing a password, deriving a slug with a uniqueness probe, computing a
denormalised rollup, dispatching outbox events on a status transition —
all of these belong in `perform_create` / `perform_update` so the call site is
explicit about what happens on this resource's create.

**Rule 2 — do override these helpers for structural cross-cutting
concerns**, layered through mixins. Stamping `created_by_id` /
`updated_by_id` from auth context, stamping `organization_id` from the
current tenant, filtering reads to non-soft-deleted rows, replacing
physical delete with a timestamp flip — all of these are safe to layer
because:

- They run *before* `save_object` (no flush-timing trap).
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
  uniqueness_probe`) → user's `perform_create` / `perform_update`, written from
  scratch (Rule 1).

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
        # Filters listing, count, AND retrieve via the framework's
        # unified read seam — no separate perform_get override needed.
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
        if org_id is not None and hasattr(obj, "organization_id"):
            obj.organization_id = org_id
        return obj
```

### `SoftDeleteMixin` — hide deleted rows

```python
from datetime import datetime, timezone


class SoftDeleteMixin:
    """Hide deleted rows; ``delete_object`` sets ``deleted_at`` instead."""

    if TYPE_CHECKING:
        request: fastapi.Request
        session: AsyncSession
        model: type[DeclarativeBase]

    def _include_deleted(self) -> bool:
        return self.request.query_params.get("include_deleted", "false").lower() == "true"

    def build_query(self) -> sa.Select:
        q = super().build_query()  # type: ignore[misc]
        if not self._include_deleted() and hasattr(self.model, "deleted_at"):
            q = q.where(self.model.deleted_at.is_(None))
        return q

    async def delete_object(self, obj: Any) -> None:
        if hasattr(obj, "deleted_at"):
            obj.deleted_at = datetime.now(timezone.utc)
            await self.session.flush()
            return
        await super().delete_object(obj)  # type: ignore[misc]
```

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
        if hasattr(obj, "created_by_id") and obj.created_by_id is None:
            obj.created_by_id = uid
        if hasattr(obj, "updated_by_id"):
            obj.updated_by_id = uid
        return obj

    async def update_object(self, obj: Any, schema_obj: Any) -> Any:
        obj = await super().update_object(obj, schema_obj)  # type: ignore[misc]
        if hasattr(obj, "updated_by_id"):
            obj.updated_by_id = self._current_user_id()
        return obj
```

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

`perform_listing` and `perform_get` consult `build_query`; `count_listing` counts
the query built by `perform_listing`. The tenant + soft-delete `WHERE` clauses
therefore apply to listing, the pagination total, **and** single-row fetches
(`GET /{id}`) without further plumbing. A row hidden from listing returns 404 from
retrieve too — and `perform_update` / `perform_delete` inherit the check
since they call `perform_get` first.

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

Admin endpoints typically don't need a separate view hierarchy. A
per-request `_is_admin()` predicate consulted by every scope-filtering
layer is the runtime-flag pattern, and it works because the mixins
already check it via `super()`. The bypass is *runtime*, not class-time
— that keeps the route tree simple but couples every read scope to an
`if not self._is_admin():` guard. The alternative (admin views opt into
a different base query, parallel `AdminProjectView` etc.) gives you
class-time guarantees at the cost of a parallel hierarchy. Pick
whichever trade-off matches the access model you actually have.

## Cross-references

- [Override Endpoints](howto_override_endpoints.md) — single-base-class
  overrides and the call chain.
- [Class-Based Views](class_based_views.md#dependency-injection-on-class-attributes)
  — the marker-based DI rule that makes mixin type stubs safe.
- [Share Behaviour with Base Views](howto_inheritance.md) — single-base
  shared logic, the simpler cousin to mixin composition.
