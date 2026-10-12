# Errors

Restly's request errors are FastAPI `HTTPException`s. FastAPI renders each
one through the exception handlers of the app, so by default every error has
FastAPI's body: `{"detail": ...}`. A database conflict in a write, such as a
duplicate unique value, becomes one of these errors too: a `409`.

Your own code raises errors in the same way. Raise one in any method of a
view, and the request stops there. If the error comes before the commit,
Restly does not commit the write.

Errors do not pass through
{meth}`to_response <fastapi_restly.views.BaseRestView.to_response>`. That
method shapes only successful responses (see
[Responses](responses.md)). To change the error body, change the
exception handlers of the app, as in any FastAPI app.

(raise-http-error)=
## Raise an HTTP error

The HTTP errors live in `fr.exc`. Each one is a subclass of
{class}`fr.exc.RestlyHTTPError <fastapi_restly.exc.RestlyHTTPError>`, which is
a `fastapi.HTTPException`:

| Exception | Status | When |
|---|---|---|
| {class}`fr.exc.NotFound <fastapi_restly.exc.NotFound>` | `404` | The row does not exist, or the [scope](scopes.md) hides it. Restly raises it for `/{id}` routes and for a [reference](relationships.md) to a missing row. |
| {class}`fr.exc.Forbidden <fastapi_restly.exc.Forbidden>` | `403` | The caller may not do this action. Raise it in {meth}`authorize <fastapi_restly.views.RestView.authorize>`. |
| {class}`fr.exc.Conflict <fastapi_restly.exc.Conflict>` | `409` | The request does not fit the current state of the row, such as publishing a post that is already published. Restly raises it for a [database conflict](#default-exception-handling) in a write. |
| {class}`fr.exc.BadQueryParam <fastapi_restly.exc.BadQueryParam>` | `400` | Restly cannot apply a list parameter, such as `?sort=unknown_field`. |

Raise them from your own code: a business method, a hook or a custom route.
The publish route of the blog example raises `Conflict` inside its write
action, so the write is not committed:

```{literalinclude} ../example-projects/blog/blog/main.py
:language: python
:start-at: '@fr.post("/{id}/publish", status_code=200)'
:end-at: return self.to_response(post)
:dedent: 4
```

The full file is
[`blog/main.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/blog/blog/main.py).
A plain `fastapi.HTTPException` works in the same places, for a status code
that has no class in `fr.exc`, such as `401`.

To check whether the caller may do an action, raise `Forbidden` from
[`authorize`](#authorize-gate). Restly calls it at the right moment, so the
business methods stay free of permission checks.

The other names in `fr.exc` are not HTTP errors.
{class}`fr.exc.RestlyError <fastapi_restly.exc.RestlyError>` and
{class}`RestlyConfigurationError <fastapi_restly.exc.RestlyConfigurationError>`
report a setup mistake. The warnings
{class}`RestlyUncommittedChangesWarning <fastapi_restly.exc.RestlyUncommittedChangesWarning>`,
{class}`RestlyMisuseWarning <fastapi_restly.exc.RestlyMisuseWarning>` and
{class}`RestlyDuplicateSchemaNameWarning <fastapi_restly.exc.RestlyDuplicateSchemaNameWarning>`
are there too.

(list-request-errors)=
## Why a bad list request returns 422 or 400

Two steps check the query string of a list endpoint, and each has its own
status code:

- `422`: the query string does not match the
  [list parameters](filtering.md). This covers an unknown key,
  such as `?nope=1`, and a value of the wrong type for a typed parameter,
  such as `?page_size=oops`.
- `400`: the key is known, but Restly cannot apply the value. Restly raises
  {class}`BadQueryParam <fastapi_restly.exc.BadQueryParam>` for a sort field
  that does not exist, and for a filter value that fails the field's
  [validation](#filter-value-validation), such as `?age=old` on an `int`
  field.

A filter value passes the first step as text. Restly checks it against the
field only when it builds the query, so a bad filter value returns `400`, not
`422`.

(default-exception-handling)=
## Unique values and other database conflicts: 409

A duplicate value in a unique column, a foreign key to a row that does not
exist, a `NULL` in a `NOT NULL` column, and a failed check constraint make
SQLAlchemy raise an `IntegrityError`, at a flush or at the commit. In a
Restly write, the request then returns `409`, not `500`. The body has
FastAPI's normal shape:

```json
{
  "detail": "Unique constraint violated on user.email"
}
```

The `detail` text depends on the database driver. Restly knows the common
PostgreSQL error codes and SQLite error messages. For other databases, it uses
a general conflict message.

The write block does this. Each CRUD write, and each
[`write_action`](#custom-action-route) block in a custom route, turns the
`IntegrityError` into
{class}`fr.exc.Conflict <fastapi_restly.exc.Conflict>`, with the
`IntegrityError` as its `__cause__`. So a handler for
{class}`fr.exc.RestlyHTTPError <fastapi_restly.exc.RestlyHTTPError>` shapes
these conflicts too; see [Change the error body](#error-envelope).

Three kinds of code get the raw `IntegrityError` instead:

- A write inside a
  [`shared_write_action_commit()`](#shared-write-action-commit) block. There
  the outermost block turns what escapes it into a `409`, so a bulk route can
  still catch the error of one row.
- Code outside a write block, such as a bare `View` route or a plain FastAPI
  route that commits itself. It owns its commit, and it must handle its own
  errors.
- {meth}`after_action_commit <fastapi_restly.views.RestView.after_action_commit>`,
  and the after-hooks that run after a shared commit. The write is already
  saved then, so a `409` would be wrong.

A custom route that catches the conflict of its own `write_action` block
catches `fr.exc.Conflict`:

```python
    @fr.post("/{id}/rename", status_code=200)
    async def rename(self, id: int, name: str):
        tag = await self.get_one(id)
        try:
            async with self.write_action("rename", obj=tag):
                tag.name = name
        except fr.exc.Conflict:
            raise fr.exc.Conflict(f"A tag named {name!r} already exists")
        return self.to_response(tag)
```

After a conflict, the session waits for a rollback. To write again in the
same request, call `await self.session.rollback()` first.

(error-envelope)=
## Change the error body for the whole app

To replace the `{"detail": ...}` body, register a handler for
{class}`fr.exc.RestlyHTTPError <fastapi_restly.exc.RestlyHTTPError>`.
FastAPI finds a handler through the class of the exception and its base
classes, so this one handler covers all four errors in the table above,
including the [database conflicts](#default-exception-handling). The
handler below returns an RFC 9457 problem details body:

```python
from fastapi.responses import JSONResponse


@app.exception_handler(fr.exc.RestlyHTTPError)
async def problem_handler(request, exc):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "type": "about:blank",
            "title": exc.detail,
            "status": exc.status_code,
        },
        media_type="application/problem+json",
    )
```

With this handler, a missing row returns
`{"type": "about:blank", "title": "Doc with id 999 was not found", "status": 404}`
with the `application/problem+json` content type. The `400` errors of the
standard list routes get the same body.

Some errors are not a `RestlyHTTPError`, so this handler does not change them:

- A `422` for a request body or a parameter that fails validation, such as
  `?page_size=oops`. This is a `fastapi.exceptions.RequestValidationError`.
  Register a handler for it.
- A plain `fastapi.HTTPException`. Restly raises one for an unknown list
  parameter (`422`), and for a foreign key and its relationship field that
  name different rows (`422`). Register a handler for
  `fastapi.HTTPException`.
- Most `400` errors of a react-admin list route, such as a bad `range`,
  `filter` or `sort` value. They are a plain `fastapi.HTTPException` too.

## See also

- [Request Lifecycle & Hooks](lifecycle.md): where
  {meth}`authorize <fastapi_restly.views.RestView.authorize>` and the business
  methods run, and when the commit happens.
- [Filtering & Pagination](filtering.md): the list parameters that
  the `422` and `400` errors check.
- {doc}`api/exc`: every name in `fr.exc`.
