# Compose Views with Mixins

Model mixins declare shared columns, such as tenant ids and audit stamps.
SQLAlchemy session listeners restrict tenant reads. Clause namespaces define
[scopes](scopes.md), such as hiding soft-deleted rows. View mixins override
shared behavior, such as setting a timestamp instead of deleting a row.

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
