# Code Review: FastAPI-Restly

## Bugs

### 1. `filter[field]=null` is broken (`_v1.py:393–397`)

```python
else:
    if filter_value == "null":
        value = None            # dead code —
    value = parser(filter_value)  # always overwrites, even when filter_value == "null"
    return column == value
```

When `filter[name]=null`, `value = None` is set then immediately overwritten by
`parser("null")`. For a string field this filters for the literal string `"null"`,
not SQL `NULL`. Same structural problem in the `!null` branch (lines 388–391):
`value = None` then `parser(None)` is called, which may or may not validate
depending on field optionality.

### 2. `offset=0` not applied in V1 pagination (`_v1.py:119`)

```python
if offset:  # falsy for 0, so offset=0 is silently skipped
    select_query = select_query.offset(offset)
```

Should be `if offset is not None:`.

### 3. `_is_string_field` in V1 misses `typing.Union` (`_v1.py:99–111`)

```python
origin = get_origin(annotation)
if origin is UnionType:   # only matches `str | None` new-style union
```

`Optional[str]` from `typing` has `get_origin() == typing.Union`, not
`types.UnionType`. Fields annotated `Optional[str]` won't get `contains[field]`
query parameters generated. The V2 version correctly handles both cases via
`_unwrap_optional_annotation`.

### 4. `AlchemyView` missing relationship eager-loading (`_sync.py`)

`AsyncAlchemyView.process_index` calls `self.get_relationship_loader_options()`
and applies them to the query. `AlchemyView.process_index` does not — relationships
are never eagerly loaded in the sync view.

Same for `process_get` (`_sync.py:144`): the async version passes
`options=loader_options` to `session.get()`; the sync version doesn't.

---

## Design Issues

### 5. `_escape_like_value` duplicated verbatim

`_v1.py:400–405` and `_v2.py:485–490` are identical. Should live in a shared
internal module.

### 6. `['value']` string-parsing hack repeated throughout `_v2.py`

```python
if value.startswith("['") and value.endswith("']"):
    value = value[2:-2]
```

This appears in 6+ places. It works around Pydantic rendering a single-element
list as its `repr()`. It's a symptom of a deeper serialization issue and is
fragile: it silently corrupts values that happen to start with `['` or end
with `']`.

### 7. Readonly check asymmetry between sync and async `make_new_object`

- `AlchemyView.make_new_object` passes `self.creation_schema` to the readonly
  check — but `creation_schema` already has readonly fields removed, so the
  check is always a no-op.
- `AsyncAlchemyView.make_new_object` passes `self.schema`, where the check
  actually does something.

Both produce the same result by accident. The sync version should use
`self.schema` for clarity, or the check should be removed from the async
version.

### 8. `make_new_object` / `update_object` standalone only for sync

`fr.make_new_object(session, model, schema_obj)` and `fr.update_object(...)` are
exported as standalone functions for the sync path. There are no async equivalents.
Composing async CRUD logic outside a view subclass requires reimplementing these
from scratch.

### 9. Auto-generated schema doesn't parameterize `IDSchema`

In `_generator.py`, when a model has an `id` field, `IDSchema` (bare, without a
type parameter) is used as a base class. This means
`_coerce_id_to_model_primary_key_type` can't infer the PK type and skips
coercion. UUID primary keys passed as strings won't be coerced to `UUID` in
auto-generated schemas. The explicit `IDSchema[MyModel]` form works; the
auto-generated path does not.

### 10. Mutable `ClassVar` defaults on `View`

```python
exclude_routes: ClassVar[list[str]] = []
responses: ClassVar[dict[int, Any]] = {404: {"description": "Not found"}}
```

These are shared mutable objects across all subclasses.
`SomeView.exclude_routes.append("delete")` would silently contaminate every other
view. The intended pattern is reassignment (e.g.
`exclude_routes = ["delete"]`), not mutation, but there's nothing enforcing this.

### 11. `Base.get_one_or_create` is `async` on a non-async base class

`Base` is used as the foundation for both sync and async models. Putting an
`async` utility method on it means sync `AlchemyView` users who inherit from
`Base` see an `async` method that can't be called in a sync context. There is no
sync counterpart.

### 12. `set_query_modifier_version` sets a `ContextVar`, not a true global

```python
def set_query_modifier_version(version: QueryModifierVersion) -> None:
    _query_modifier_version.set(version)
```

The name implies "set this globally", but `ContextVar.set()` only affects the
current execution context. In asyncio apps, tasks copy the context at creation
time. Calling this inside a running request handler changes the version only for
that request. Calling it at module import time (the common case) works as
expected, but the semantics are surprising and the docs don't address this.

### 13. `V1Interface` / `V2Interface` add nothing

Both classes in `_config.py` inherit from `_FunctionQueryModifierInterface`
without adding any behaviour or fields. They exist only as type tokens but are
never used in `isinstance` checks. They should either be removed or given a
purpose.

---

## Documentation Issues

### 14. `pytest_plugins` path inconsistent across docs

`howto_testing.md` documents:
```python
pytest_plugins = ["fastapi_restly.pytest_fixtures"]
```

`pytest_fixtures.md` documents:
```python
pytest_plugins = ["fastapi_restly.testing._fixtures"]
```

Both work, but one is the public module and one is a private implementation
detail. The docs should consistently point to `fastapi_restly.pytest_fixtures`.

### 15. `TESTING.md` referenced but doesn't exist

Both `getting_started.md` and `tutorial.md` end with a "Next Steps" link to
`TESTING.md` at the repository root. No such file exists.

### 16. `TimestampsMixin` docstring says "timezone naive"

```python
class TimestampsMixin(MappedAsDataclass, kw_only=True):
    """Mixin to add created_at and updated_at timestamps (timezone naive)."""
```

`utc_now()` returns `datetime.now(timezone.utc)` — a timezone-aware datetime.
The docstring is wrong.

### 17. `howto_custom_schema.md` schema has a field absent from the model

```python
class User(fr.IDBase):
    first_name: Mapped[str]
    email: Mapped[str]

class UserSchema(fr.IDSchema):
    first_name: str = Field(alias="firstName")
    email: str
    internal_id: fr.ReadOnly[str]  # not on the model
```

`to_response_schema` silently skips fields the ORM object doesn't have, so
`internal_id` is absent from responses without any error or warning. As a how-to
guide this is misleading — it implies the pattern works end-to-end.

---

## Minor Issues

- `_client.py:26` has a bare `except:` — should be `except Exception:`.
- `getattrs`, `rebase_with_model_config`, `set_schema_title` are exported from
  `schemas/__init__.py` but not from `fastapi_restly.__init__`. The public API
  surface is inconsistent.
- V1 and V2 query implementations are two ~400-line parallel files sharing
  almost no code. Column resolution, value parsing, and clause building are all
  duplicated. An internal shared module would reduce future drift between them.
