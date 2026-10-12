# Compose Views with Mixins

Model mixins declare shared columns, such as tenant ids and audit stamps.
SQLAlchemy session listeners restrict tenant reads. Clause namespaces define
[scopes](scopes.md), such as hiding soft-deleted rows. View mixins override
shared behavior, such as setting a timestamp instead of deleting a row.

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

## Reusing logic outside the view

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
    schema = UserSchema

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
shadows a dependency, but it can still shadow inherited attribute lookups
in some setups. `if TYPE_CHECKING:` is the safe wrapper for both.

## Admin bypass: runtime flag, not a parallel view tree

Read scope is *visibility*, not *policy*. Rows outside the
[scope](scopes.md) return 404;
allow/deny decisions such as "only managers may create" belong in
{meth}`authorize <fastapi_restly.views.RestView.authorize>`, described in
[`authorize`: gate the action](customize.md#authorize-gate-the-action).

## Cross-references

- [Request Lifecycle & Hooks](customize.md): how a view handles a request,
  single-base overrides, and the call chain.
- [Views](class_based_views.md#dependency-injection-on-class-attributes):
  the marker-based DI rule that makes mixin type stubs safe.
- [Share Behaviour with Base Views](howto_inheritance.md): single-base
  shared logic, the simpler cousin to mixin composition.
