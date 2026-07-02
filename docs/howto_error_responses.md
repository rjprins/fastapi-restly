# Shape Error Responses

Restly's request-time errors are ordinary FastAPI `HTTPException`s, so by
default every error renders as FastAPI's standard `{"detail": ...}` body.
This page covers the typed exceptions your overrides should raise, and how to
change the error envelope app-wide.

One rule applies throughout: errors bypass
{meth}`to_response <fastapi_restly.views.BaseRestView.to_response>`. The
response boundary on a view shapes *successful* payloads (see
[Response Envelopes and List Metadata](howto_response_schema.md)); error
shaping happens at FastAPI's exception-handler layer, app-wide, exactly as in
a plain FastAPI app.

## The typed exceptions

All request-time errors live in `fr.exc` and subclass {class}`fr.exc.RestlyHTTPError <fastapi_restly.exc.RestlyHTTPError>`
(itself a `fastapi.HTTPException`):

| Exception | Status | Raised when / raise it for |
|---|---|---|
| {class}`fr.exc.NotFound <fastapi_restly.exc.NotFound>` | `404` | A row does not exist, or is hidden by {meth}`build_query <fastapi_restly.views.RestView.build_query>` scoping. |
| {class}`fr.exc.Forbidden <fastapi_restly.exc.Forbidden>` | `403` | {meth}`authorize <fastapi_restly.views.RestView.authorize>` rejects the action. |
| {class}`fr.exc.Conflict <fastapi_restly.exc.Conflict>` | `409` | The request conflicts with current resource state. |
| {class}`fr.exc.BadQueryParam <fastapi_restly.exc.BadQueryParam>` | `400` | A list-endpoint parameter that is structurally valid but semantically wrong (e.g. `?sort=unknown_field`). |

Raise them from your own overrides (`authorize`, business verbs, custom
routes), and they render through whatever handler is installed:

```python
class ArticleView(fr.AsyncRestView):
    ...

    async def authorize(self, action, obj=None, data=None):
        if action == "delete" and not self.request.state.is_admin:
            raise fr.exc.Forbidden("deletes need an admin token")
```

The remaining names in `fr.exc` are not HTTP errors:
{class}`fr.exc.RestlyError <fastapi_restly.exc.RestlyError>` and
{class}`RestlyConfigurationError <fastapi_restly.exc.RestlyConfigurationError>`
are setup-time framework errors, and the warnings
{class}`RestlyUncommittedChangesWarning <fastapi_restly.exc.RestlyUncommittedChangesWarning>` and
{class}`RestlyMisuseWarning <fastapi_restly.exc.RestlyMisuseWarning>` also
live there.

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

## Database conflicts: `IntegrityError` to 409

Not every conflict response comes from your own code: Restly installs a
default handler that translates SQLAlchemy `IntegrityError`s into
`409 Conflict` responses. It respects a handler you registered yourself, and
it can be disabled with
{func}`fr.configure(app=app, install_default_exception_handlers=False) <fastapi_restly.db.configure>`;
the exact registration contract is in
[Default Exception Handling](api_reference.md#default-exception-handling).

## See also

- [How Overrides Work](the_handle_design.md): where {meth}`authorize <fastapi_restly.views.RestView.authorize>` and the
  business verbs sit; a raised exception skips the commit bracket.
- [Filter, Sort, and Paginate Lists](howto_query_modifiers.md): the
  parameter grammar whose violations produce the 422/400 split.
