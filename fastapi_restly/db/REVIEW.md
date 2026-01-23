# DB Module API Review

Review date: 2026-01-23

## Summary

The `db` module provides database connection setup, session management, and global session proxies for both async and sync SQLAlchemy operations. The overall design is reasonable for a convention-over-configuration framework, but there are several issues that should be addressed.

## What Works Well

1. **Proxy pattern** - The `AsyncSessionProxy` and `SessionProxy` classes allow importing `AsyncSession`/`Session` before database initialization. Users get clean imports that work seamlessly.

2. **Layered flexibility** - Setup functions accept URL, engine, or sessionmaker, covering both simple cases and advanced customization needs.

3. **Testing utilities** - The `activate_savepoint_only_mode()` function provides valuable test isolation that many frameworks lack.

4. **Type annotations** - Generally well-typed with proper generics throughout.

## Issues

### Critical

#### `db_lifespan` is broken (`_session.py:81-92`)

The function references undefined variables `engine` and `Base`:

```python
async with engine.begin() as conn:  # engine doesn't exist in scope
    await conn.run_sync(Base.metadata.create_all)  # Base doesn't exist
```

This will crash at runtime. Either remove it or implement it properly.

### High Priority

#### Naming collision with SQLAlchemy

`AsyncSession` and `Session` shadow SQLAlchemy's own classes:

```python
from fastapi_restly.db import AsyncSession  # returns proxy object
from sqlalchemy.ext.asyncio import AsyncSession  # returns the actual class
```

This will cause confusion and potential bugs when users mix imports. Consider:
- Lowercase names: `async_session` / `session` (like Flask-SQLAlchemy's `db.session`)
- More distinct names: `get_async_session` / `get_session`
- Factory pattern: `create_async_session()` / `create_session()`

#### Error message typo (`_session.py:40-41`)

The error message references wrong function name:

```python
raise Exception(
    "set_sessionmaker() requires either ..."  # Should be setup_async_database_connection()
)
```

### Medium Priority

#### Inconsistent sessionmaker configuration

Async and sync sessionmakers have different defaults:

| Setting | Async | Sync |
|---------|-------|------|
| `autoflush` | `False` | default (`True`) |
| `expire_on_commit` | `False` | `False` |

This inconsistency may cause subtle behavior differences between async and sync code paths.

#### Session generator auto-commit behavior (`_session.py:141-162`)

The auto-commit logic runs unconditionally after yield:

```python
yield session
if session.is_active:
    await session.commit()
```

If an exception is raised in the route handler, this still attempts to commit. Consider whether this is the intended behavior or if exceptions should trigger a rollback instead.

#### Large export surface (`__init__.py`)

The module exports both instances and their classes:
- `AsyncSession` (instance) and `AsyncSessionProxy` (class)
- `Session` (instance) and `SessionProxy` (class)

Users rarely need the proxy classes directly. Consider keeping `AsyncSessionProxy` and `SessionProxy` as internal implementation details (prefix with `_` or remove from `__all__`).

### Low Priority

#### Function naming clarity

`_get_sync_engine()` works on both async and sync sessionmakers but the name suggests sync-only. Consider renaming to `_get_engine()` or `_extract_sync_engine()`.

#### Missing docstrings

The proxy classes have class-level docstrings but their methods (`__call__`, `begin`, `kw`) lack documentation.

#### Global state limitations

`fr_globals` as a module-level singleton limits multi-app scenarios (e.g., one application using two different databases). This is a known tradeoff for convention-over-configuration frameworks, but worth documenting.

## Recommendations

1. **Immediate**: Fix or remove the broken `db_lifespan` function
2. **Immediate**: Fix the error message typo
3. **Short-term**: Resolve the naming collision with SQLAlchemy
4. **Short-term**: Make sessionmaker configuration consistent between async/sync
5. **Consider**: Reduce the public API surface by hiding proxy classes
6. **Consider**: Review auto-commit behavior in session generators for exception handling
