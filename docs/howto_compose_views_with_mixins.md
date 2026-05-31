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

## The structural override points

Three [override points](howto_override_endpoints.md) carry almost all
structural concerns:

- `build_query` — the unified **read scope**. List, count, and retrieve
  all route through it, so one `.where(...)` clause filters every read.
- `prepare_create` / `prepare_update` — **cooperative field stamping**.
  Each returns a `dict` of *extra* server-controlled fields to set on the
  object; the framework applies them. Mixins layer by calling `super()`,
  merging their fields into the dict.
- the `delete` business verb — to replace a physical delete with a flag
  flip (soft delete).

These are deliberately not the place for per-view *application* logic.

**Rule 1 — don't use these override points for per-view application
logic.** Hashing a password, deriving a slug with a uniqueness probe,
computing a denormalised rollup, dispatching outbox events on a status
transition — all of these belong in a per-view `create` / `update`
business verb so the call site is explicit about what happens on this
resource's write. (See
[Override Endpoints](howto_override_endpoints.md) for that pattern.)

**Rule 2 — do use these override points for structural cross-cutting
concerns**, layered through mixins. Stamping `created_by_id` /
`updated_by_id` from auth context, stamping `organization_id` from the
current tenant, filtering reads to non-soft-deleted rows, replacing
physical delete with a timestamp flip — all of these are safe to layer
because:

- `prepare_create` / `prepare_update` return *extra* fields rather than
  mutating a half-built object, so they compose cleanly and never fight
  the schema's own writes.
- They run inside the commit-free business verb, before the handler's
  commit, so there's no flush-timing trap.
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

A per-view `create` / `update` override is an instance method — it has
access to `self.session`, `self.request`, and any view state mixins
inject. That's almost always what you want, and the view is the right
home for the logic.

On the rare occasion the same logic must also run from a script or a
background job, extract a plain function and call it from both. Put the
function wherever makes sense — same module as the view, an adjacent
helper, a small `Client` class — pick the obvious spot; don't manufacture
a layer for it:

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

`create` is commit-free — the request handler owns the commit — so
setting `password_hash` here persists correctly. The old "mutate after
save" trap is gone: there is no flush between `make_new_object` and
`save_object` that could strand your write.

The `make_new_object` / `save_object` instance methods are thin wrappers
over the `async_make_new_object` / `async_save_object` free functions in
`fastapi_restly.objects`; import those when you need the same behaviour
from a worker with a bare session instead of a view.

Don't preempt this. Most business logic only ever runs from the view;
extract a function when the second caller actually exists, not before.

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

    async def prepare_create(self, schema_obj: Any) -> dict[str, Any]:
        fields = await super().prepare_create(schema_obj)  # type: ignore[misc]
        org_id = self._current_org_id()
        if org_id is not None and hasattr(self.model, "organization_id"):
            fields["organization_id"] = org_id
        return fields
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

The soft-delete flip overrides the bare `delete` business verb, not the
handler. That keeps it auth-free and commit-free: `handle_delete` still
loads the row through `get_one` (so it 404s on an already-hidden row),
runs `authorize`, and owns the commit — the mixin only changes *what
"delete" means* for this view. Calling `save_object` (rather than
committing) leaves the commit to the handler, so `after_commit` hooks
still fire after durability.

### `AuditStampedMixin` — record who created/updated each row

```python
class AuditStampedMixin:
    """Stamp ``created_by_id`` / ``updated_by_id`` from request state."""

    if TYPE_CHECKING:
        request: fastapi.Request

    def _current_user_id(self) -> int | None:
        return getattr(self.request.state, "user_id", None)

    async def prepare_create(self, schema_obj: Any) -> dict[str, Any]:
        fields = await super().prepare_create(schema_obj)  # type: ignore[misc]
        uid = self._current_user_id()
        fields["created_by_id"] = uid
        fields["updated_by_id"] = uid
        return fields

    async def prepare_update(self, obj: Any, schema_obj: Any) -> dict[str, Any]:
        fields = await super().prepare_update(obj, schema_obj)  # type: ignore[misc]
        fields["updated_by_id"] = self._current_user_id()
        return fields
```

Each mixin's `prepare_create` / `prepare_update` starts by calling
`super()` to collect the fields lower layers contributed, adds its own
keys, and returns the merged dict. The framework sets every key on the
object after building/applying the schema, so stamps never collide with
schema-driven writes and the layers compose in any order. `prepare_update`
takes the already-loaded `obj` as well as the update schema, so a mixin
can compare against the current row if it needs to.

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

`get_many` and `get_one` consult `build_query`; `count` counts the query
built for the list. The tenant + soft-delete `WHERE` clauses therefore
apply to listing, the pagination total, **and** single-row fetches
(`GET /{id}`) without further plumbing. A row hidden from the list returns
404 from retrieve too — and update/delete inherit the check, because
`handle_update` / `handle_delete` load the target through `get_one` first.

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

Read scope is *visibility*; it is not a substitute for *policy*. A row
hidden by `build_query` 404s for everyone, which is the right default,
but coarse allow/deny decisions ("only managers may create") belong in
`authorize`, consulted by the request handlers. See
[Override Endpoints](howto_override_endpoints.md) for the `authorize`
override point.

## Cross-references

- [Override Endpoints](howto_override_endpoints.md) — the three tiers,
  single-base overrides, and the call chain.
- [Class-Based Views](class_based_views.md#dependency-injection-on-class-attributes)
  — the marker-based DI rule that makes mixin type stubs safe.
- [Share Behaviour with Base Views](howto_inheritance.md) — single-base
  shared logic, the simpler cousin to mixin composition.
