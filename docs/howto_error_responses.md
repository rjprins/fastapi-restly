# Errors

Restly's request-time errors are ordinary FastAPI `HTTPException`s, so by
default every error renders as FastAPI's standard `{"detail": ...}` body.
This page covers the typed exceptions your overrides should raise, and how to
change the error envelope app-wide.

One rule applies throughout: errors bypass
{meth}`to_response <fastapi_restly.views.BaseRestView.to_response>`. The
response boundary on a view shapes *successful* payloads (see
[Responses](howto_response_schema.md)); error
shaping happens at FastAPI's exception-handler layer, app-wide, exactly as in
a plain FastAPI app.

## The typed exceptions

All request-time errors live in `fr.exc` and subclass {class}`fr.exc.RestlyHTTPError <fastapi_restly.exc.RestlyHTTPError>`
(itself a `fastapi.HTTPException`):

| Exception | Status | Raised when / raise it for |
|---|---|---|
| {class}`fr.exc.NotFound <fastapi_restly.exc.NotFound>` | `404` | A row does not exist, or is outside the view's [scope](scopes.md). |
| {class}`fr.exc.Forbidden <fastapi_restly.exc.Forbidden>` | `403` | {meth}`authorize <fastapi_restly.views.RestView.authorize>` rejects the action. |
| {class}`fr.exc.Conflict <fastapi_restly.exc.Conflict>` | `409` | The request conflicts with current resource state. |
| {class}`fr.exc.BadQueryParam <fastapi_restly.exc.BadQueryParam>` | `400` | A list-endpoint parameter that is structurally valid but semantically wrong (e.g. `?sort=unknown_field`). |

Raise them from your own overrides (`authorize`, business methods, custom
routes), and they render through whatever handler is installed:

```python
class ArticleView(fr.AsyncRestView):
    ...
    current_user: Annotated[User, Depends(get_current_user)]

    async def authorize(self, action, obj=None, data=None):
        if action == "delete" and not self.current_user.is_admin:
            raise fr.exc.Forbidden("deletes need an admin token")
```

The remaining names in `fr.exc` are not HTTP errors:
{class}`fr.exc.RestlyError <fastapi_restly.exc.RestlyError>` and
{class}`RestlyConfigurationError <fastapi_restly.exc.RestlyConfigurationError>`
are setup-time framework errors, and the warnings
{class}`RestlyUncommittedChangesWarning <fastapi_restly.exc.RestlyUncommittedChangesWarning>`,
{class}`RestlyMisuseWarning <fastapi_restly.exc.RestlyMisuseWarning>` and
{class}`RestlyDuplicateSchemaNameWarning <fastapi_restly.exc.RestlyDuplicateSchemaNameWarning>`
also live there.

## Raise HTTP errors from any method

Every method runs inside a request context, so you can raise `fastapi.HTTPException` (or {class}`fr.exc.Forbidden <fastapi_restly.exc.Forbidden>` / {class}`fr.exc.NotFound <fastapi_restly.exc.NotFound>`) at any point:

```python
import fastapi

    async def create(self, schema_obj):
        if not self.current_user.is_admin:
            raise fastapi.HTTPException(403, "Admin access required")
        return await super().create(schema_obj)
```

For permission gating specifically, prefer [`authorize`](#authorize-gate); it runs at the right phase of the handler and keeps the business method auth-free.

## 422 vs 400 on list endpoints

Two layers reject bad query strings on list endpoints, each with its own
status code:

- FastAPI's request validation returns `422` for requests that fail the
  [schema-derived parameters](howto_query_modifiers.md): unknown keys
  (`?nope=1`), and type-invalid values for typed parameters
  (`?page_size=oops`).
- Restly's query application raises
  {class}`BadQueryParam <fastapi_restly.exc.BadQueryParam>` (`400`) for
  parameters that passed validation but cannot be applied, such as an
  unresolvable sort field or an invalid filter path.

## Change the error envelope app-wide

To replace the default `{"detail": ...}` body, register a handler for
{class}`fr.exc.RestlyHTTPError <fastapi_restly.exc.RestlyHTTPError>`; because
exception handlers match subclasses, one handler covers all four typed
errors. The handler below renders an RFC 9457 problem-details envelope:

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

With that installed, a hidden row renders as
`{"type": "about:blank", "title": "Doc with id 999 was not found", "status": 404}`
with the `application/problem+json` content type, including the `400`
query-parameter errors. To also cover FastAPI's
own `422` validation errors and plain `HTTPException`s raised elsewhere,
register handlers for `RequestValidationError` and `HTTPException` the
standard FastAPI way.

(default-exception-handling)=
## Database conflicts: `IntegrityError` to 409

Not every conflict response comes from your own code. Restly installs a
default handler that translates a SQLAlchemy `IntegrityError` into a
`409 Conflict` response. This covers unique constraint, foreign-key,
not-null, and check-constraint violations. The body has FastAPI's normal
error shape:

```json
{
  "detail": "Unique constraint violated on user.email"
}
```

The exact `detail` text is best-effort and depends on the database driver.
The handler recognizes common PostgreSQL SQLSTATE integrity codes and SQLite
constraint messages. For other databases, it uses a generic conflict message.

Restly installs the handler in either of these cases:

- `fr.configure(app=app, ...)` is called with the default
  `install_default_exception_handlers=True`.
- A view is registered directly on a `FastAPI` app with `fr.include_view(app)`.
  This covers apps that configure database sessions separately.

Restly skips the handler when the app already has a handler for
`sqlalchemy.exc.IntegrityError`. A generic handler does not count.

To turn the handler off, pass `install_default_exception_handlers=False`:

```python
fr.configure(
    app=app,
    async_database_url="sqlite+aiosqlite:///app.db",
    install_default_exception_handlers=False,
)
```

To use your own response format, register your own `IntegrityError` handler
before Restly installs its defaults:

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

## See also

- [Request Lifecycle & Hooks](customize.md): where {meth}`authorize <fastapi_restly.views.RestView.authorize>` and the
  business methods sit; a raised exception skips the commit bracket.
- [Filtering & Pagination](howto_query_modifiers.md): the
  parameter grammar whose violations produce the 422/400 split.
