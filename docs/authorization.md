# Authorization

[A read](#read-request-steps) above split read
access into two independent concerns; each has its own override point:

- Visibility, meaning which rows exist at all for this caller, lives in the
  view scope: a [query clause](clauses.md) declared as the model's
  `default_scope` or the view's
  {attr}`scope <fastapi_restly.views.BaseRestView.scope>`.
  [Scopes](scopes.md) owns that topic.
- Policy, meaning whether this caller may perform the action, lives in {meth}`authorize <fastapi_restly.views.RestView.authorize>`, which the handler calls.

(authorize-gate)=
## `authorize`: gate the action

{meth}`authorize(action, obj=None, data=None) <fastapi_restly.views.RestView.authorize>` runs inside `handle_<verb>`: before {meth}`create <fastapi_restly.views.RestView.create>` and {meth}`get_many <fastapi_restly.views.RestView.get_many>`, and after the object is loaded for {meth}`get_one <fastapi_restly.views.RestView.get_one>` / {meth}`update <fastapi_restly.views.RestView.update>` / {meth}`delete <fastapi_restly.views.RestView.delete>`. Override it to enforce policy:

```python
from typing import Annotated
from fastapi import Depends

@fr.include_view(app)
class InvoiceView(fr.AsyncRestView):
    prefix = "/invoices"
    model = Invoice
    schema = InvoiceSchema
    current_user: Annotated[User, Depends(get_current_user)]

    async def authorize(self, action, obj=None, data=None):
        if action in ("create", "update", "delete") and not self.current_user.is_staff:
            raise fr.exc.Forbidden()
        if action == "update" and obj.posted:
            raise fr.exc.Forbidden("Posted invoices are immutable")
```

`action` is the verb, `obj` is the loaded row, and `data` is the validated request payload. You add authentication yourself; Restly calls `authorize` and maps {class}`fr.exc.Forbidden <fastapi_restly.exc.Forbidden>` / {class}`fr.exc.NotFound <fastapi_restly.exc.NotFound>` to HTTP responses.

`current_user` is a class-level dependency, injected per request like any other ([Dependency injection on class attributes](class_based_views.md#dependency-injection-on-class-attributes)). {attr}`self.request <fastapi_restly.views.BaseRestView.request>` is the live FastAPI `Request`. {attr}`self.session <fastapi_restly.views.RestView.session>` is the injected async SQLAlchemy session. Both are available in every method.

Visibility belongs in the [scope](scopes.md), not here: raising from `authorize` produces a 403, whereas a row outside the scope produces a 404.

Read scope is *visibility*, not *policy*. Rows outside the
[scope](scopes.md) return 404;
allow/deny decisions such as "only managers may create" belong in
{meth}`authorize <fastapi_restly.views.RestView.authorize>`, described in
[`authorize`: gate the action](#authorize-gate).
