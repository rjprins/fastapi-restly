(current)=
# Request Context

The request context holds values, such as the id of the user who sends the
request, that any code in the request can read. In your app, it is a class,
usually named `Current`. Bind a value once, then read it wherever you need
it:

```python
import fastapi_restly as fr


class Current(fr.ContextNamespace):
    user_id: fr.ContextParam[int]


with Current.bind(user_id=42):
    print(Current.user_id())  # 42
```

`Current` is a class that you declare in your application, usually in
`app/current.py`, as [New & Existing Projects](#project-current) shows. Its
base, {class}`fr.ContextNamespace <fastapi_restly.clauses.ContextNamespace>`,
gives it the binding methods. Each annotation with
{class}`fr.ContextParam <fastapi_restly.clauses.ContextParam>` declares one
value, a member. Import the same `Current` wherever you use it.

There are two ways to bind a member:

- For each request, a FastAPI dependency binds it from your authentication:
  [Bind from a FastAPI dependency](#current-request-binding).
- Around any block of code, such as a script or a job, `Current.bind(...)`
  binds it: [Bind in a script or job](#current-manual-binding).

Call the member to read it: `Current.user_id()` returns the bound value.
Without a binding, it raises `LookupError`. A binding lasts until its `with`
block exits, or until the request ends. `Current.user_id == 42` and
`if Current.user_id:` raise `TypeError`, because without the call they would
test the member, not the bound value.

Pass the member without calling it where the value is read later. In a
[query clause](#binding-values), the member is a placeholder that gets the
bound value each time the clause is applied. In a
[column default](#current-column-defaults), SQLAlchemy calls the member when
it writes the row.

Use `Current` for values that code across the application needs, such as the
user who acts. Keep ordinary function inputs as arguments. A function that
reads `Current` needs a binding, even though its signature does not show it.

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

Trust this header only when an authenticating proxy replaces the values that
clients send, and clients cannot reach the app directly.

FastAPI calls `get_user_id` before the endpoint. The generated dependency
binds the result for the request. Both `def` and `async def` endpoints can
read it. Binding only supplies the value. It does not check who the caller is,
and it does not decide what the caller may do: that is
[`authorize`](#authorize-gate).

:::{admonition} Details
:class: details

`SetCurrentContextDep` is already a {func}`Depends <fastapi.Depends>` object.
Add it directly to `dependencies`, without another `Depends(...)` around it.

Attach the dependency to the app when every route needs it. Use the
`dependencies` of an `APIRouter`, or a view's
{attr}`dependencies <fastapi_restly.views.View.dependencies>`, for a smaller
group of routes. Each source can be a callable, a `Depends(...)` object, or
an `Annotated` dependency alias.

One `Current.depends(...)` call can bind several members, each from its own
dependency. The SaaS example binds four members from its authentication in
[`app/current.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/current.py).
:::

(current-column-defaults)=
## Read the current user in a column default

A SQLAlchemy column default can read `Current`. Pass the member itself,
without parentheses:

```python
created_by_id: Mapped[int] = mapped_column(
    init=False, insert_default=Current.user_id
)
```

SQLAlchemy calls the member when it inserts the row, usually during a flush.
So keep the binding active until the flush finishes. `Current.user_id()` with
parentheses would read the value once, when Python defines the model class.
No request has bound a value then, so the import fails with `LookupError`.

[Stamp the fields the server owns](#server-stamped-fields) shows the whole
recipe, with `onupdate` and `fr.ReadOnly` on the schema.

(current-manual-binding)=
## Bind in a script or job

Outside FastAPI, use
{meth}`Current.bind <fastapi_restly.clauses.ContextNamespace.bind>` around
the code that reads the values. This standalone example writes a row with a
column default that reads `Current.user_id`:

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

import fastapi_restly as fr


class Current(fr.ContextNamespace):
    user_id: fr.ContextParam[int]


class Base(fr.ModelDefaults, DeclarativeBase):
    pass


class Note(fr.IDMixin, Base):
    title: Mapped[str]
    created_by_id: Mapped[int] = mapped_column(
        init=False, insert_default=Current.user_id
    )


engine = create_engine("sqlite://")
Base.metadata.create_all(engine)

with Current.bind(user_id=42), Session(engine) as session:
    note = Note(title="Meeting notes")
    session.add(note)
    session.commit()
    assert note.created_by_id == 42
```

A worker receives its input in the job payload. Bind those values in the
worker before it calls code that reads `Current`.

(current-binding-lifetime)=
## How long a binding lasts

An inner binding replaces an outer value until its block exits. Then the
previous value comes back, also when an exception leaves the block:

```python
with Current.bind(user_id=42):
    with Current.bind(user_id=7):
        assert Current.user_id() == 7
    assert Current.user_id() == 42
```

Bindings use {class}`contextvars.ContextVar`, not class attributes that the
whole process shares. So two requests that run at the same time each see
their own values. An async task gets a copy of the context in which it is
created. When the parent's binding ends, the task keeps its copy. Bind
explicitly when you start work for a different user.

(current-database-sessions)=
## Put the database session in Current

You can bind a SQLAlchemy {class}`Session <sqlalchemy.orm.Session>` or
{class}`AsyncSession <sqlalchemy.ext.asyncio.AsyncSession>` to a
`Current.session` member. This works, but we do not recommend it. Pass the
session as an argument to the functions that use the database.

Coroutines that you start with {func}`asyncio.create_task` or
{func}`asyncio.gather` get the current bindings. If `Current.session` is
bound, they get the same session object, not separate sessions. Give
database tasks that run at the same time separate sessions. SQLAlchemy's
[async scoped-session guidance](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#using-asyncio-scoped-session)
also recommends passing sessions as arguments in new asyncio code.

:::{admonition} Details
:class: details

`Current.session` works like SQLAlchemy's
{class}`scoped_session <sqlalchemy.orm.scoped_session>`: code finds the
session in the context instead of receiving it as an argument. `Current`
only holds the session that you bind. It does not create sessions, and it
does not manage their transactions.

When code finds the session in the context, it is harder to see which
operations share one transaction. Helpers that look independent can use the
same session. A commit then includes the pending changes of all those
helpers. Keep it clear which code creates, commits, rolls back and closes
the session. See SQLAlchemy's
[session lifecycle guidance](https://docs.sqlalchemy.org/en/20/orm/session_basics.html#when-do-i-construct-a-session-when-do-i-commit-it-and-when-do-i-close-it).
:::

(current-testing)=
## Use another user in a test

FastAPI's `app.dependency_overrides` still works on the source dependency.
Override the source, and the generated dependency binds the value that the
override returns:

```python
from fastapi.testclient import TestClient

app.dependency_overrides[get_user_id] = lambda: 7
try:
    with TestClient(app) as client:
        assert client.get("/me").json() == {"user_id": 7}
finally:
    app.dependency_overrides.pop(get_user_id)
```

A test that calls application code directly, without a request, binds the
values with `Current.bind(...)` around the call. That does not test the
request dependency, so keep a test that goes through a request too.
[Testing](testing.md) covers the test setup.

(current-members)=
## Declare and share context members

Add one `name: fr.ContextParam[T]` annotation on `Current` for each value.
The attribute name is also the keyword that `bind()` and `depends()` take.
An unknown name raises `TypeError`. `T` is the type that a call of the member
returns. A manual binding does not check or convert the value, so validate
input where it enters the application.

Use a separate namespace for a value that only one part of the application
uses. To share a member between namespaces, assign the existing member:

```python
class ReportContext(fr.ContextNamespace):
    user_id = Current.user_id
    locale: fr.ContextParam[str]


with ReportContext.bind(user_id=42, locale="nl"):
    assert Current.user_id() == 42
    assert ReportContext.locale() == "nl"
```

`ReportContext.user_id` is the same member as
`Current.user_id`. A new `user_id: fr.ContextParam[int]`
annotation would create a separate member instead.

:::{admonition} Details
:class: details

A subclass inherits the members of its parent. Start the names of helper
methods with `_`, because every public attribute must be a member.

A `bind()` or `depends()` call must use one name for each member. Two names
for the same member raise `TypeError`, even when their values are equal.

A nullable annotation, such as `fr.ContextParam[int | None]`, gives no
default value. Bind `None` explicitly when "no value" is a valid value. A
read without a binding still raises `LookupError`.
:::

(current-explain)=
## See what is bound

{meth}`Current.explain <fastapi_restly.clauses.ContextNamespace.explain>`
lists each member with its value, and the file and line that bound it.
A member without a binding shows as `UNBOUND`:

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

For a generated dependency, the line is the `Current.depends(...)` call.
Read this output when a value is missing or not what you expect.

## See also

- [Query Clauses](#binding-values): use the same members in query clauses
  and scopes.
- [Models](#server-stamped-fields): stamp the author on every write.
- [Multi-Tenancy](tenancy.md): stamp the tenant on every write, and keep
  each tenant's rows apart.
- [Authorization](#authorize-gate): decide what the caller may do.
