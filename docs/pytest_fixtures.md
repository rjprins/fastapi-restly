# TODO, obviously

### 1. **Support shared sync+async sessions if user uses `psycopg`**

* Document this clearly: *“To share a DB connection between sync and async tests (for nested transactions, visibility, etc.), use the `psycopg` driver in both engines.”*
* Provide a helper for wrapping the sync connection into an async one.

```python
def wrap_sync_connection_for_async(sync_conn, async_engine):
    from sqlalchemy.ext.asyncio import AsyncConnection
    return AsyncConnection(async_engine, sync_connection=sync_conn)
```

Let advanced users opt in.

---

### 2. **Gracefully fall back for `psycopg2`/`asyncpg` users**

* If sync and async drivers are different, just don’t share the connection.
* Document that they won’t get shared transactions/savepoints in that case.
* This still lets them write reliable tests, just without the visibility guarantee between sync and async sessions.

---

### 3. **Detect and warn (optional)**

You can even do a driver-level check and raise/log a warning:

```python
if type(sync_conn.connection.connection) is not type(async_conn.sync_connection.connection.connection):
    warnings.warn("Sync and async sessions do not share the same DBAPI connection. Use psycopg to enable this.")
```

---

## 💡 Suggested doc phrasing

> **Note**: If you want your sync and async test sessions to share the same database connection (e.g. to see each other's writes in the same transaction), use the `psycopg` driver for both engines:
>
> * `postgresql+psycopg://...` for both `create_engine` and `create_async_engine`
>
> Other combinations like `psycopg2` + `asyncpg` are supported, but do **not** allow shared DB-level state across sync/async boundaries.

