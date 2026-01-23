# Models Module API Report

## Overview

The models module provides SQLAlchemy 2.0 base classes and mixins for building database models with dataclass integration. It exports 10 public symbols.

## Components

### Base Classes

| Class | Purpose |
|-------|---------|
| `Base` | Root declarative base with dataclass support, auto table naming, and enum handling |
| `IDBase` | Base + auto-incrementing integer primary key |
| `IDStampsBase` | IDBase + created_at/updated_at timestamps |

### Mixins

| Mixin | Purpose |
|-------|---------|
| `IDMixin` | Adds `id: Mapped[int]` primary key |
| `TimestampsMixin` | Adds `created_at` and `updated_at` columns |
| `TableNameMixin` | Auto-generates `__tablename__` from class name |

### Constants

| Constant | Value |
|----------|-------|
| `CASCADE_ALL_ASYNC` | `"save-update, merge, delete, expunge"` |
| `CASCADE_ALL_DELETE_ORPHAN_ASYNC` | Above + `", delete-orphan"` |

### Utilities

| Function | Purpose |
|----------|---------|
| `utc_now()` | Returns timezone-naive UTC datetime |
| `underscore(name)` | Converts CamelCase to snake_case |

---

## API Design Analysis

### Strengths

1. **Good layering**: The inheritance hierarchy (`Base` -> `IDBase` -> `IDStampsBase`) lets users choose the right level of functionality.

2. **Async-aware cascade constants**: Documenting and providing async-safe cascade options is helpful and prevents a common pitfall with `refresh-expire`.

3. **Sensible defaults**:
   - Enums stored as strings (avoids migration headaches)
   - Auto table naming from class name
   - Dataclass integration via `MappedAsDataclass`

4. **Clean exports**: The `__all__` list is well-organized and matches the imports.

### Concerns

1. **`utc_now()` strips timezone info**
   ```python
   return datetime.now(timezone.utc).replace(tzinfo=None)
   ```
   Returning naive datetimes can cause confusion. Consider keeping the timezone or documenting this choice prominently.

2. **`get_one_or_create` is async-only**
   - Lives on `Base`, but only works with async sessions
   - No sync equivalent provided
   - Users of `AlchemyView` (sync) can't use it without confusion

3. **`underscore()` edge case handling**
   ```python
   >>> underscore("HTTPRequest")
   'httprequest'  # Expected: 'http_request'
   ```
   Consecutive uppercase letters don't get separated. This affects table names for models like `HTTPLog` -> `httplog` instead of `http_log`.

4. **`TableNameMixin` return type**
   ```python
   def __tablename__(cls) -> Any:
   ```
   The return type should be `str`, not `Any`.

5. **Mixin ordering sensitivity**
   The mixins use `MappedAsDataclass` with `kw_only=True`. This requires careful MRO ordering when combining with other mixins or bases. Not documented.

6. **Missing `__repr__` / `__str__`**
   No default string representation is provided. Models will show as `<ClassName object at 0x...>` which is unhelpful for debugging.

### Minor Observations

- `CASCADE_ALL_ASYNC` names are long. A shorter alias like `CASCADE_ASYNC` might be more ergonomic.
- The `enum.Enum` in `type_annotation_map` has `length=64` hardcoded. Users with longer enum values will hit truncation.

---

## Recommendations

1. Add a sync version of `get_one_or_create` or move the async version to a mixin that's clearly async-only.

2. Consider a `__repr__` mixin that shows model class name and primary key.

3. Fix `underscore()` to handle consecutive uppercase:
   ```python
   # "HTTPRequest" -> "http_request"
   ```

4. Document MRO requirements for mixing `TimestampsMixin` with custom bases.

5. Consider whether stripping timezone from `utc_now()` is the right default, or at least document the rationale clearly.

---

## Summary

The models module provides a solid foundation with sensible defaults. The main API surface is clean and the layered base classes are well thought out. The concerns raised are relatively minor and mostly relate to edge cases and documentation rather than fundamental design issues.

**Rating**: Good API design with room for polish.
