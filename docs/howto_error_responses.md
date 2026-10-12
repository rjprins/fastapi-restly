# Errors

Restly's request errors are FastAPI `HTTPException`s. FastAPI renders each
one through the exception handlers of the app, so by default every error has
FastAPI's body: `{"detail": ...}`. Restly installs one handler of its own. It
turns a database conflict, such as a duplicate unique value, into a `409`
response.

Your own code raises errors in the same way. Raise one in any method of a
view, and the request stops there. If the error comes before the commit,
Restly does not commit the write.

Errors do not pass through
{meth}`to_response <fastapi_restly.views.BaseRestView.to_response>`. That
method shapes only successful responses (see
[Responses](howto_response_schema.md)). To change the error body, change the
exception handlers of the app, as in any FastAPI app.

(raise-http-error)=
## Raise an HTTP error

The HTTP errors live in `fr.exc`. Each one is a subclass of
{class}`fr.exc.RestlyHTTPError <fastapi_restly.exc.RestlyHTTPError>`, which is
a `fastapi.HTTPException`:

| Exception | Status | When |
|---|---|---|
| {class}`fr.exc.NotFound <fastapi_restly.exc.NotFound>` | `404` | The row does not exist, or the [scope](scopes.md) hides it. Restly raises it for `/{id}` routes and for a [reference](howto_relationship_idschema.md) to a missing row. |
| {class}`fr.exc.Forbidden <fastapi_restly.exc.Forbidden>` | `403` | The caller may not do this action. Raise it in {meth}`authorize <fastapi_restly.views.RestView.authorize>`. |
| {class}`fr.exc.Conflict <fastapi_restly.exc.Conflict>` | `409` | The request does not fit the current state of the row, such as publishing a post that is already published. |
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
  [list parameters](howto_query_modifiers.md). This covers an unknown key,
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

Restly installs a handler that turns a SQLAlchemy `IntegrityError` into a
`409 Conflict` response. So a duplicate value in a unique column, a foreign
key to a row that does not exist, a `NULL` in a `NOT NULL` column, and a
failed check constraint each return `409`, not `500`. The body has FastAPI's normal shape:

```json
{
  "detail": "Unique constraint violated on user.email"
}
```

The `detail` text depends on the database driver. The handler knows the
common PostgreSQL error codes and SQLite error messages. For other databases,
it uses a general conflict message.

Restly installs the handler in two places:

- {func}`fr.configure(app=app, ...) <fastapi_restly.db.configure>`, unless
  you pass `install_default_exception_handlers=False`.
- `fr.include_view(app)`, when you register a view directly on a `FastAPI`
  app. This covers an app that does not pass itself to `fr.configure`.

Restly does not install the handler when the app already has a handler for
`sqlalchemy.exc.IntegrityError`. A handler for `Exception` does not count.

To return your own body for these conflicts, register your own
`IntegrityError` handler first, before `fr.configure` and before you register
views on the app:

```python
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError


@app.exception_handler(IntegrityError)
async def integrity_error_handler(request, exc):
    return JSONResponse(
        status_code=409,
        content={"error": {"code": "constraint_conflict"}},
    )


fr.configure(app=app, async_database_url="sqlite+aiosqlite:///app.db")
```

`install_default_exception_handlers=False` stops only `fr.configure` from
installing the handler. `fr.include_view(app)` still installs it. If you
register views directly on the app and want no handler, register your own
`IntegrityError` handler, as above.

(error-envelope)=
## Change the error body for the whole app

To replace the `{"detail": ...}` body, register a handler for
{class}`fr.exc.RestlyHTTPError <fastapi_restly.exc.RestlyHTTPError>`.
FastAPI finds a handler through the class of the exception and its base
classes, so this one handler covers all four errors in the table above. The
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
- The `409` of the database conflict handler above.
- Most `400` errors of a react-admin list route, such as a bad `range`,
  `filter` or `sort` value. They are a plain `fastapi.HTTPException` too.

## See also

- [Request Lifecycle & Hooks](lifecycle.md): where
  {meth}`authorize <fastapi_restly.views.RestView.authorize>` and the business
  methods run, and when the commit happens.
- [Filtering & Pagination](howto_query_modifiers.md): the list parameters that
  the `422` and `400` errors check.
- {doc}`api/exc`: every name in `fr.exc`.
