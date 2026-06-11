# Shape Error Responses

Restly's request-time errors are ordinary FastAPI `HTTPException`s, so by
default every error renders as FastAPI's standard `{"detail": ...}` body.
This page covers the typed exceptions your overrides should raise, and how to
change the error envelope app-wide.

One rule up front: **errors bypass `to_response`**. The response boundary on a
view shapes *successful* payloads; error shaping happens at FastAPI's
exception-handler layer, app-wide, exactly like in a plain FastAPI app.

## The typed exceptions

All request-time errors live in `fr.exc` and subclass `fr.exc.RestlyHTTPError`
(itself a `fastapi.HTTPException`):

| Exception | Status | Raised when / raise it for |
|---|---|---|
| `fr.exc.NotFound` | `404` | A row doesn't exist — or is hidden by `build_query` scoping. |
| `fr.exc.Forbidden` | `403` | `authorize` rejects the action. |
| `fr.exc.Conflict` | `409` | The request conflicts with current resource state. |
| `fr.exc.BadQueryParam` | `400` | A list-endpoint parameter that is structurally valid but semantically wrong (e.g. `?sort=unknown_field`). |

Raise them from your own overrides — `authorize`, business verbs, custom
routes — and they render through whatever handler is installed:

```python
class ArticleView(fr.AsyncRestView):
    ...

    async def authorize(self, action, obj=None, data=None):
        if action == "delete" and not self.request.state.is_admin:
            raise fr.exc.Forbidden("deletes need an admin token")
```

(`fr.exc.RestlyError` / `RestlyConfigurationError` are setup-time framework
errors, not HTTP errors; the warnings `RestlyUncommittedChangesWarning` and
`RestlyMisuseWarning` also live in `fr.exc`.)

## 422 vs 400 on list endpoints

Two layers reject bad query strings, with different statuses:

- **`422`** — FastAPI's request validation, against the schema-derived
  parameters: unknown keys (`?nope=1`), and type-invalid values for typed
  parameters (`?page_size=oops`).
- **`400 BadQueryParam`** — Restly's query application, for parameters that
  passed validation but cannot be applied: an unresolvable sort field, an
  invalid filter path.

## Change the error envelope app-wide

Register a handler for `fr.exc.RestlyHTTPError` — subclass matching means one
handler covers all four typed errors. An RFC 9457 problem-details envelope:

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
with the `application/problem+json` content type — including the `400`
query-parameter errors. To also cover FastAPI's own `422` validation errors
and plain `HTTPException`s raised elsewhere, register handlers for
`RequestValidationError` and `HTTPException` the standard FastAPI way.

## Database conflicts (`IntegrityError` → 409)

Restly installs a default handler that translates SQLAlchemy
`IntegrityError`s into `409 Conflict` responses. It respects a handler you
registered yourself and can be disabled with
`fr.configure(app=app, install_default_exception_handlers=False)` — the exact
registration contract is in
[Default Exception Handling](api_reference.md#default-exception-handling).

## See also

- [How Overrides Work](the_handle_design.md) — where `authorize` and the
  business verbs sit; a raised exception skips the commit bracket.
- [Filter, Sort, and Paginate Lists](howto_query_modifiers.md) — the
  parameter grammar whose violations produce the 422/400 split.
