# Critical Review of FastAPI-Restly

## 1. Operator Precedence Bug in V1 Filters (Real Bug)

`fastapi_restly/query/_v1.py:386-397` — The `>=` and `<=` checks are **unreachable**:

```python
if filter_value.startswith(">"):       # matches ">=" too!
    ...
elif filter_value.startswith("<"):     # matches "<=" too!
    ...
elif filter_value.startswith(">="):    # DEAD CODE — never reached
    ...
elif filter_value.startswith("<="):    # DEAD CODE — never reached
```

`>=` always matches `>` first. The `>=` and `<=` operators literally cannot work.

## 2. Global Mutable State for Everything

The framework relies on module-level singletons everywhere:

- `fr_globals` (`_globals.py:14`) — holds the database sessionmaker
- `_query_modifier_version` (`_config.py:41`) — global query version
- `settings` (`_settings.py:13`) — disconnected Settings object

This means:
- **You cannot run two apps with different databases in the same process.** Multi-tenancy, multiple test suites, or running two FastAPI sub-applications with different DBs is impossible.
- **Tests must carefully reset global state** — the conftest.py already shows this pain with `reset_metadata`.
- Thread safety is not guaranteed.

## 3. `settings` Is Completely Disconnected

`_settings.py` defines `Settings` with `async_database_url` and `database_url`, and it's exported in `__init__.py`. But **nothing in the framework reads from it**. `setup_async_database_connection()` takes its own URL argument and stores it in `fr_globals`. Two sources of truth, one of which does nothing.

## 4. ILIKE Wildcard Injection in `contains` Filters

`_v1.py:332` and `_v2.py:455`:
```python
column.ilike(f"%{filter_value}%")
```

The `%` and `_` characters are ILIKE wildcards and aren't escaped. A user sending `contains[name]=%` matches everything. Not SQL injection (SQLAlchemy parameterizes), but it's a semantic bypass of the intended "substring search" behavior.

## 5. ID Type Hardcoded to `int`

Every CRUD method signature uses `id: int`:
- `_async.py:64` — `async def get(self, id: int)`
- `_async.py:95` — `async def patch(self, id: int, ...)`
- `_async.py:109` — `async def delete(self, id: int)`

UUID primary keys are extremely common. Using the framework with UUIDs requires overriding every single endpoint method, defeating the purpose of auto-generation.

## 6. `async_resolve_ids_to_sqlalchemy_objects` Has Wrong Type Annotation

`_base.py:72-73`:
```python
async def async_resolve_ids_to_sqlalchemy_objects(
    session: SA_Session, schema_obj: BaseSchema  # SA_Session = sync Session!
```

This async function annotates its session parameter as `SA_Session` (the sync session) but then calls `await session.get_one(...)`. Should be `SA_AsyncSession`.

## 7. `WriteOnly` Fields Are Not Filtered from Responses

`WriteOnly[T]` is documented and exists, but `to_response_schema()` (`_base.py:184-205`) never filters them out. It builds a payload dict from the ORM object and calls `self.schema.model_validate(payload)`. If the schema has a `password: WriteOnly[str]` field and the ORM object has a `password` attribute, it will appear in the API response. The `writeOnly` in `json_schema_extra` is just OpenAPI documentation — it doesn't actually hide the field.

## 8. Dead Code with a Bug

`_base.py:255-258`:
```python
async def _excluded_route(self, *args, **kwargs):
    raise NotImplementedError(
        "This route has been excluded from {self.__class__.__name__}"
    )
```

This function is never called (the actual exclusion mechanism deletes `_api_route_args` instead). And it has a bug — it uses a regular string, not an f-string, so `{self.__class__.__name__}` would appear literally.

## 9. `MappedAsDataclass` as the Only Option Is Very Constraining

`Base` inherits from `MappedAsDataclass` (`_base.py:62`). This forces all user models into dataclass semantics:
- Field order matters (required before optional)
- Mutable defaults need `default_factory`
- You can't use plain class-level attributes
- Inheritance is finicky with field ordering

This is a significant constraint that isn't highlighted in docs. Standard SQLAlchemy `DeclarativeBase` would be more flexible while still supporting the same features.

## 10. Massive Duplication Between V1 and V2 Query Systems

`_v1.py` (~410 lines) and `_v2.py` (~460 lines) duplicate most logic: column resolution, nested schema handling, value parsing, clause building. The `_config.py` adapter pattern instantiates throwaway `V1Interface`/`V2Interface` classes inside a function on every call. This should be a shared base with strategy-specific overrides, not two parallel implementations.

## 11. `process_index` Ignores Its Own `query_params` Argument (Async)

`_async.py:40-61` — The `index()` endpoint receives `query_params` from FastAPI (a Pydantic-validated model) and passes it to `process_index()`. But `process_index` then ignores it:

```python
query = apply_query_modifiers(
    self.request.query_params,  # uses raw Starlette QueryParams instead
    query, self.model, self.schema
)
```

The Pydantic validation on `query_params` runs but its result is thrown away. The sync `AlchemyView` does it differently — it passes `query_params` directly. This inconsistency means the validated data is never used in async mode.

## 12. No Pagination Metadata

The list endpoint returns a bare JSON array. No total count, no page info, no next/prev links. For any real API this is a significant gap — clients have no way to know if there are more results.

## 13. `utc_now()` Creates Misleading Naive Datetimes

`_base.py:26`:
```python
def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
```

Creates a UTC datetime then strips the timezone. This produces naive datetimes that are semantically UTC but not marked as such, which causes comparison issues with timezone-aware datetimes and makes the intent invisible to downstream code.

## 14. Inconsistent Import Convention

CLAUDE.md and README say `import fastapi_restly as fr`, but the test suite uses `import fastapi_restly as fd`. The blog example uses the deprecated `fr.AsyncSession` instead of `fr.FRAsyncSession`. The `__init__.py` re-exports `mapped_column` from SQLAlchemy (but not `Mapped`, `relationship`, or `ForeignKey` which users still need from SQLAlchemy directly).

## 15. `get()` Decorator Doesn't Set `methods=["GET"]`

`_base.py:119-124`: The `get()` decorator just calls `route()` without setting `methods`. The docstring says "Equivalent to: @route(path, methods=["GET"], status_code=200)" but it doesn't actually do either. It works because FastAPI defaults to GET, but it's inconsistent with `post()`, `put()`, `patch()`, and `delete()` which all explicitly set their HTTP method.

---

## Summary

The two most concerning issues are the **operator precedence bug** (#1, `>=`/`<=` filters are broken) and the **WriteOnly not actually working** (#7, passwords could leak). The global state design (#2) is the biggest architectural limitation — it prevents multi-tenancy and makes testing fragile. The hardcoded `int` ID type (#5) significantly limits the framework's applicability.

The framework has a solid core idea and the layered philosophy is sound, but it needs a pass to fix the real bugs and to reconsider the global state approach before a 1.0 release.
