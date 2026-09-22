(current)=
# Current context

`Current` makes values such as the authenticated user's id available to
code throughout a request. Bind the value once, then read it wherever it
is needed:

```python
import fastapi_restly as fr


class Current(fr.ContextNamespace):
    user_id: fr.ContextParam[int]


with Current.bind(user_id=42):
    print(Current.user_id())  # 42
```

`Current.user_id()` returns the bound value. Reading it without a binding
raises `LookupError`. A binding lasts until its `with` block exits.

Call the member to read it. `Current.user_id == 42` and `if Current.user_id:`
raise `TypeError`: without the call they would test the member object, not
the bound value.

`Current` is a class you define in your application, conventionally in
`app/current.py`. Its base,
{class}`fr.ContextNamespace <fastapi_restly.clauses.ContextNamespace>`,
provides binding and dependency integration. Each annotated
{class}`fr.ContextParam <fastapi_restly.clauses.ContextParam>` declares
one value. Import the same `Current` wherever it is used.

Use `Current` for values that code across the application needs, such as
the acting user. Keep ordinary function inputs as arguments. A function
that reads `Current` requires a binding even though its signature does
not mention one.

(current-request-binding)=
## Bind from a FastAPI dependency

Pass your existing authentication dependency to
{meth}`Current.depends <fastapi_restly.clauses.ContextNamespace.depends>`.
This example reads the `X-User-ID` header. It rejects a request without
that header:

```python
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException


def get_user_id(
    user_id: Annotated[int | None, Header(alias="X-User-ID")] = None,
) -> int:
    if user_id is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user_id


SetCurrentContextDep = Current.depends(user_id=get_user_id)

app = FastAPI(dependencies=[SetCurrentContextDep])


@app.get("/me")
def me() -> dict[str, int]:
    return {"user_id": Current.user_id()}
```

Only trust this header when an authenticating proxy replaces client-supplied
values and clients cannot reach the app directly.

`SetCurrentContextDep` is already a {func}`Depends <fastapi.Depends>` object.
Add it directly to `dependencies`, without another `Depends(...)` wrapper.

FastAPI calls `get_user_id` before the endpoint. The generated dependency
binds its result for the request. Both `def` and `async def` endpoints
can read it. Binding supplies the value, it does not authenticate the
caller or perform an authorization check.

Attach the dependency to the app when every route needs it. Use an
`APIRouter`'s `dependencies` or a view's
{attr}`dependencies <fastapi_restly.views.View.dependencies>` for a
smaller group of routes. Each source can be a callable, a `Depends(...)`
object, or an `Annotated` dependency alias.

`Current.depends(...)` binds one member or several, each from its own
dependency, in one declaration.

(current-column-defaults)=
## Read the current user in a column default

Pass `Current.user_id` as a SQLAlchemy column default to record who
created a row:

```python
from sqlalchemy.orm import Mapped, mapped_column


class Note(fr.IDBase):
    title: Mapped[str]
    created_by_id: Mapped[int] = mapped_column(
        init=False,
        insert_default=Current.user_id,
    )
```

Pass `Current.user_id` without parentheses. SQLAlchemy calls it when
inserting the row, usually during a flush. Keep the binding active until
the flush finishes. Writing `Current.user_id()` would read the value
when the model class is defined, before a request has bound anything.

Use `onupdate=Current.user_id` for an update stamp.
[Compose Views with Mixins](howto_compose_views_with_mixins.md) covers
audit fields and their schemas. For tenancy, use the
[SQLAlchemy session rule](#tenant-row-scoping) described there.

(current-manual-binding)=
## Bind in a script or job

Outside FastAPI, use
{meth}`Current.bind <fastapi_restly.clauses.ContextNamespace.bind>` around
the code that reads the values. This standalone database example uses
the `Note` model above:

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

engine = create_engine("sqlite://")
Note.metadata.create_all(engine)

with Current.bind(user_id=42), Session(engine) as session:
    note = Note(title="Meeting notes")
    session.add(note)
    session.commit()
    assert note.created_by_id == 42
```

A worker receives its input through the job payload. Bind those values
in the worker before calling code that reads `Current`.

(current-binding-lifetime)=
## Restore values after a nested binding

An inner binding temporarily replaces an outer value. Exiting the block
restores the previous value, including when an exception leaves the block:

```python
with Current.bind(user_id=42):
    with Current.bind(user_id=7):
        assert Current.user_id() == 7
    assert Current.user_id() == 42
```

Bindings use {class}`contextvars.ContextVar`, not process-wide mutable
class attributes. Async tasks inherit the context in which they are
created. Resetting a parent binding does not clear a task's inherited
context. Bind explicitly when starting work for a different user.

(current-database-sessions)=
## Database sessions

You can bind a SQLAlchemy {class}`Session <sqlalchemy.orm.Session>` or
{class}`AsyncSession <sqlalchemy.ext.asyncio.AsyncSession>` to a
`Current.session` member. This is not inherently wrong and can be
convenient, but it is not the recommended pattern. Prefer passing
the session explicitly to functions that use the database.

This use of `Current.session` is comparable to SQLAlchemy's
{class}`scoped_session <sqlalchemy.orm.scoped_session>`: callers look up
the session from context instead of receiving it as an argument.
`Current` only holds the supplied session. It does not create sessions
or manage their transactions.

Implicit access can obscure which operations belong to the same database
transaction. Helpers that look independent can use the same session.
Committing it includes pending changes from all those helpers.
Keep responsibility for creating, committing, rolling back, and closing
the session explicit. See SQLAlchemy's
[session lifecycle guidance](https://docs.sqlalchemy.org/en/20/orm/session_basics.html#when-do-i-construct-a-session-when-do-i-commit-it-and-when-do-i-close-it).

Coroutines scheduled with {func}`asyncio.create_task` or
{func}`asyncio.gather` inherit the current bindings by default.
If `Current.session` is bound, they receive the same session object,
not separate sessions. Give concurrent database tasks separate sessions.
SQLAlchemy's
[async scoped-session guidance](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#using-asyncio-scoped-session)
also recommends passing sessions explicitly for new asyncio code.

(current-testing)=
## Override the source in an API test

FastAPI's `app.dependency_overrides` still addresses the original source:

```python
from fastapi.testclient import TestClient

app.dependency_overrides[get_user_id] = lambda: 7
try:
    with TestClient(app) as client:
        assert client.get("/me").json() == {"user_id": 7}
finally:
    app.dependency_overrides.pop(get_user_id)
```

For a test that calls application code directly, use `Current.bind(...)`
around the call. A binding in the test's context is not a replacement for
testing the request dependency.

(current-members)=
## Declare and share context members

Add one `name: fr.ContextParam[T]` annotation per value on `Current`.
The attribute name is also the keyword accepted by `bind()` and
`depends()`. Unknown names raise `TypeError`. `T` describes the return
type of the member's call. Manual binding does not validate or coerce
the value, so validate inputs where they enter the application.

Use a separate namespace when a value belongs to one part of the
application. Assign an existing member to share its binding:

```python
class ReportContext(fr.ContextNamespace):
    user_id = Current.user_id
    locale: fr.ContextParam[str]


with ReportContext.bind(user_id=42, locale="nl"):
    assert Current.user_id() == 42
    assert ReportContext.locale() == "nl"
```

`ReportContext.user_id` is the same member as `Current.user_id`.
Declaring a new `user_id: fr.ContextParam[int]` instead would create an
independent member. Subclasses inherit their parent's members. Prefix
helper methods with `_`, since public members must be context values.

Use only one name per member in a `bind()` or `depends()` call. Two names
for the same member raise `TypeError`, even if their values are equal.

A nullable annotation such as `fr.ContextParam[int | None]` does not
provide a default. Bind `None` explicitly when absence is a valid value.
An unbound read still raises `LookupError`.

(current-explain)=
## Inspect a binding

{meth}`Current.explain <fastapi_restly.clauses.ContextNamespace.explain>`
lists each member's value and the file and line that bound it.
Unbound members are shown as `UNBOUND`:

```python
# app/example.py
with Current.bind(user_id=42):
    print(Current.explain())

print(Current.explain())
```

Output:

```text
Current
└─ user_id = 42   bound at app/example.py:2 (<module>)
Current
└─ user_id: UNBOUND
```

For a generated dependency, the origin names the `Current.depends(...)`
declaration. Inspect this output when a value is missing or unexpected.

```{seealso}
[Using Current in query clauses](#shared-params) explains how query
fragments consume the same values.
```
