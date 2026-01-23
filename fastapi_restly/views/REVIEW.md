# Views Module API Analysis

A technical review of the `fastapi_restly/views/` module API design.

---

## Overview

The views module provides a class-based view (CBV) system for FastAPI, inspired by `fastapi-utils`. It offers:

- A base `View` class with route decorators (`@get`, `@post`, `@put`, `@delete`)
- `AsyncAlchemyView` and `AlchemyView` for CRUD operations on SQLAlchemy models
- Auto-registration via `@include_view(app)`
- Auto-generation of Pydantic schemas from SQLAlchemy models

The overall design is clean and provides a good developer experience for common REST API patterns.

---

## Strengths

### 1. Intuitive Decorator API
The route decorators mirror FastAPI's own decorators, making the API familiar:

```python
@fr.include_view(app)
class UserView(fr.AsyncAlchemyView):
    prefix = "/users"
    model = User

    @get("/{id}/profile")  # Custom endpoint alongside CRUD
    async def profile(self, id: int): ...
```

### 2. Good Separation of Concerns
The endpoint/process method pattern is well-designed:

```python
@get("/{id}")
async def get(self, id: int):
    return await self.process_get(id)

async def process_get(self, id: int):
    # Override this for custom logic
```

This allows developers to either:
- Override `process_get` to modify business logic while keeping the endpoint signature
- Override `get` entirely for full control

### 3. Schema Auto-Generation
The automatic creation of `creation_schema` (without read-only fields) and `update_schema` (with optional fields) from the base schema reduces boilerplate significantly.

### 4. Flexible Registration
Supporting both decorator and function call styles is user-friendly:

```python
@include_view(app)
class MyView: ...

# or
include_view(app, MyView)
```

### 5. Proper Endpoint Isolation
The `_copy_all_parent_class_endpoints_into_this_subclass()` function correctly handles the challenge of subclass-specific annotations without polluting parent classes.

---

## Issues and Concerns

### 1. Inconsistent `query_params` Handling (Bug)

**Location:** `_sync.py:64-65`

The sync `index` method accepts `query_params` but doesn't pass it to `process_index`:

```python
# _sync.py
@get("/")
def index(self, query_params: Any) -> Sequence[Any]:
    return self.process_index()  # query_params not passed!

def process_index(self, query: sqlalchemy.Select[Any] | None = None) -> Sequence[Any]:
    # No query_params parameter
```

Compare to async version which correctly passes it:

```python
# _async.py
@get("/")
async def index(self, query_params: Any) -> Sequence[Any]:
    return await self.process_index(query_params)  # Passed correctly

async def process_index(self, query_params: pydantic.BaseModel, ...):
    # Has query_params parameter
```

### 2. Inconsistent Read-Only Field Filtering

**Location:** `_sync.py:16-26` vs `_async.py:121-138`

The async `make_new_object` filters read-only fields:

```python
# _async.py - Correct
async def make_new_object(self, schema_obj: BaseSchema) -> Base:
    data = {}
    for field_name, value in schema_obj:
        is_readonly = is_readonly_field(self.schema, field_name)
        if is_readonly:
            continue
        data[field_name] = value
    obj = self.model(**data)
```

But the sync standalone function doesn't:

```python
# _sync.py - Missing read-only filtering
def make_new_object(session, model_cls, schema_obj, schema_cls=None):
    data = dict(schema_obj)  # No filtering!
    obj = model_cls(**data)
```

### 3. Unused `_excluded_route` Function

**Location:** `_base.py:223-226`

This function is defined but never called anywhere:

```python
async def _excluded_route(self, *args, **kwargs):
    raise NotImplementedError(
        "This route has been excluded from {self.__class__.__name__}"
    )
```

Note: The f-string is also missing the `f` prefix.

### 4. Method Names Shadow Builtins/Decorators

The CRUD methods (`get`, `post`, `put`, `delete`) shadow both Python's builtin `del`/`delete` and the imported decorators. While this works due to scoping, it can cause confusion:

```python
from ._base import get, post, put, delete  # decorators

class AsyncAlchemyView:
    @get("/{id}")           # decorator
    async def get(self, id): ...  # method with same name
```

Consider alternatives like `show`, `create`, `update`, `destroy` (Rails-style) or `retrieve`, `create`, `update`, `destroy` (DRF-style).

### 5. PUT vs PATCH Semantics

**Location:** `_async.py:94-101`

The `put` method performs partial updates (only updates provided fields), which is typically PATCH behavior:

```python
async def process_put(self, id: int, schema_obj: BaseSchema) -> Any:
    """Handle a PUT request... This should (partially) update..."""
```

REST conventions:
- **PUT**: Replace entire resource (all fields required)
- **PATCH**: Partial update (only provided fields)

The current implementation is PATCH-like but uses PUT. Consider either:
- Renaming to `patch`/`process_patch`
- Adding a separate PATCH endpoint
- Documenting this deviation clearly

### 6. Fragile `exclude_routes` Implementation

**Location:** `_base.py:229-237`

The implementation deletes `_api_route_args` from methods:

```python
def _exclude_routes(cls):
    for method_name in cls.exclude_routes:
        view_func = getattr(cls, method_name)
        del view_func._api_route_args  # Mutates shared state?
```

This could cause issues if the same method object is shared. The `_copy_all_parent_class_endpoints_into_this_subclass()` call happens before `before_include_view()`, so this should be safe, but the dependency on call order is implicit.

### 7. Generic `Any` Return Types

**Location:** `_async.py:35, 62, 78, 91`

Several methods use `Any` return type:

```python
async def index(self, query_params: Any) -> Sequence[Any]:
async def get(self, id: int) -> Any:
async def post(self, schema_obj: BaseSchema) -> Any:
```

While these are annotated at runtime via `_annotate()`, having `Any` in the source makes static analysis less useful.

---

## Minor Observations

### 1. Missing Type Annotation

**Location:** `_sync.py:41`

```python
def save_object(session, obj: Base) -> Base:  # session has no type
```

Should be:

```python
def save_object(session: Session, obj: Base) -> Base:
```

### 2. Docstring in `_base.py` References Wrong Module

**Location:** `_base.py:1-14`

The module docstring mentions `AsyncAlchemyView` but `_base.py` only contains `View` and `BaseAlchemyView`. The async implementation is in `_async.py`.

### 3. XXX Comment Left in Code

**Location:** `_sync.py:83`

```python
# XXX: Ideally use query_params argument instead of request.query_params
```

This TODO should be addressed or tracked.

---

## Suggestions

### 1. Unify Async/Sync Implementations

Consider using a base mixin or protocol to ensure both implementations stay in sync. The current duplication has led to inconsistencies.

### 2. Add PATCH Support

```python
@patch("/{id}")
async def patch(self, id: int, schema_obj: BaseSchema) -> Any:
    return await self.process_patch(id, schema_obj)
```

### 3. Consider a `ViewSet` Pattern

For users who want more control, a lower-level `ViewSet` that doesn't auto-register routes could be useful:

```python
class UserViewSet(fr.AsyncAlchemyViewSet):
    # Define actions but don't auto-register
    ...

# Manual registration
app.include_router(UserViewSet.as_router(prefix="/users"))
```

### 4. Add Hooks for Common Patterns

```python
class UserView(AsyncAlchemyView):
    async def before_create(self, schema_obj): ...
    async def after_create(self, obj): ...
    async def before_update(self, obj, schema_obj): ...
    async def after_update(self, obj): ...
```

---

## Summary

The views API is well-designed overall, with good ergonomics and sensible defaults. The main issues are:

| Priority | Issue |
|----------|-------|
| High | Sync `process_index` doesn't receive `query_params` |
| High | Sync `make_new_object` doesn't filter read-only fields |
| Medium | PUT performs partial update (PATCH semantics) |
| Low | Method names shadow decorators |
| Low | Unused `_excluded_route` function |

The architecture is sound and the pattern of separating endpoints from processing logic is good. Addressing the async/sync inconsistencies should be the priority.
