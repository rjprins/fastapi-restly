# Schemas Module API Review

**Date:** 2026-01-23
**Module:** `fastapi_restly/schemas/`
**Files reviewed:** `__init__.py`, `_base.py`, `_generator.py`

---

## Overview

The schemas module provides Pydantic schema utilities for the FastAPI-Restly framework, including:
- `ReadOnly` / `WriteOnly` type markers for field access control
- Schema transformation functions (create/update model generation)
- Auto-generation of Pydantic schemas from SQLAlchemy models
- ID resolution utilities for relationship handling

---

## Strengths

### 1. Elegant ReadOnly/WriteOnly Implementation
The use of `Annotated` types with marker objects is a clean, Pythonic approach:
```python
ReadOnly = Annotated[_T, readonly_marker, Field(json_schema_extra={"readOnly": True})]
```
This automatically propagates to OpenAPI schemas with proper `readOnly`/`writeOnly` flags.

### 2. Composable Mixins
`OmitReadOnlyMixin` and `PatchMixin` leverage `__pydantic_init_subclass__` for clean composition:
```python
# Just mix in to get behavior
class CreateUser(OmitReadOnlyMixin, UserSchema): pass
```

### 3. Generic IDSchema
`IDSchema[SQLAlchemyModel]` provides type-safe ID references with runtime model access via `get_sql_model_annotation()`.

### 4. Both Sync and Async Support
`resolve_ids_to_sqlalchemy_objects` and `async_resolve_ids_to_sqlalchemy_objects` provide parity for both session types.

### 5. Auto-generation Works Well
Tests demonstrate the schema generator handles common cases (timestamps, defaults, basic types) effectively.

---

## Issues and Concerns

### 1. Inconsistent Naming Convention
```python
is_readonly_field()      # underscore style
is_field_readonly()      # alias for consistency?
is_field_writeonly()     # different pattern (no is_writeonly_field)
```
**Impact:** Confusing API - users may not know which to use.
**Recommendation:** Pick one convention and deprecate the other.

### 2. Large Public API Surface (34 exports)
The `__all__` list includes many low-level utilities that should be internal:
- `readonly_marker`, `writeonly_marker` - implementation details
- `getattrs` - generic utility, not schema-specific
- `_get_writable_field_definitions` is private but similar to public `get_writable_inputs`

**Recommendation:** Reduce to ~15 essential exports, prefix others with `_`.

### 3. BaseSchema is Empty
```python
class BaseSchema(pydantic.BaseModel):
    # TODO: Is this still needed?
    pass
```
Either add value (common config, serialization settings) or remove the indirection.

### 4. Type Annotation Bug
```python
async def async_resolve_ids_to_sqlalchemy_objects(
    session: SA_Session,  # Wrong! Should be AsyncSession
    ...
)
```
The type hint says synchronous `Session` but the function uses `await`.

### 5. Schema Generator Type Mapping Issues

In `convert_sqlalchemy_type_to_pydantic()`:
```python
type_mapping = {
    "datetime": "datetime",  # Returns string, not type
    "UUID": "UUID",          # Same problem
    ...
}
```
Some entries return strings instead of actual types. The code later handles datetime specially but misses others.

### 6. Mutation of Schema Objects
`resolve_ids_to_sqlalchemy_objects` mutates the input schema in-place:
```python
setattr(schema_obj, field, sql_model_obj)
```
**Concern:** Side effects can cause subtle bugs. Consider returning a new dict or copy.

### 7. Missing Documentation
Several functions lack docstrings:
- `_is_readonly`
- `rebase_with_model_config`
- `_get_writable_field_definitions`

### 8. Potential Circular Reference Issue
In `create_schema_from_model`, recursive calls for relationships use:
```python
target_schema = create_schema_from_model(
    field_info["target_model"],
    include_relationships=False,  # Prevents infinite recursion
)
```
This works but loses nested relationship data. No cache/memo for repeated models.

---

## API Categorization Suggestion

### Tier 1: Core Public API (should be in `__all__`)
- `ReadOnly`, `WriteOnly` - type markers
- `BaseSchema`, `IDSchema`, `IDStampsSchema` - base classes
- `TimestampsSchemaMixin` - common mixin
- `create_schema_from_model` - schema generation
- `create_model_without_read_only_fields` - create schema derivation
- `create_model_with_optional_fields` - update/patch schema derivation
- `get_writable_inputs` - extract input fields
- `resolve_ids_to_sqlalchemy_objects`, `async_resolve_ids_to_sqlalchemy_objects`

### Tier 2: Advanced/Internal (consider prefixing with `_`)
- `OmitReadOnlyMixin`, `PatchMixin` - implementation details
- `get_read_only_fields`, `get_write_only_fields` - inspection utilities
- `is_field_readonly`, `is_field_writeonly` - field checks
- `readonly_marker`, `writeonly_marker` - should be private
- `getattrs`, `set_schema_title`, `rebase_with_model_config` - utilities
- All `_generator.py` helpers except `create_schema_from_model`

---

## Minor Observations

1. **Operator precedence bug** in `_base.py:263`:
   ```python
   new_doc = (model_cls.__doc__ or "" + "\nRead-only fields...")
   ```
   Should be: `(model_cls.__doc__ or "") + "\nRead-only fields..."`

2. **SQLAlchemyModel TypeVar** defined in `_base.py` but not exported or reused.

3. **Empty list edge case**: `resolve_ids_to_sqlalchemy_objects` with an empty list of IDSchemas would fail on `value[0].get_sql_model_annotation()`.

---

## Summary

The schemas module provides solid functionality with an elegant core design (ReadOnly/WriteOnly markers, mixins). The main areas for improvement are:

1. **API consistency** - standardize naming conventions
2. **API surface reduction** - hide implementation details
3. **Type safety** - fix the async session type hint
4. **Schema generator robustness** - improve type mapping

The module is functional and well-tested for common use cases. The issues identified are mostly about API polish rather than fundamental design flaws.
