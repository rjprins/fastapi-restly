# Authorization

Restly asks one method whether the caller may perform an action:
{meth}`authorize(action, obj=None, data=None) <fastapi_restly.views.RestView.authorize>`.
The default does nothing, so every action is allowed. Override it on a view,
and raise {class}`fr.exc.Forbidden <fastapi_restly.exc.Forbidden>` to refuse
an action. The client then gets a 403.

Restly calls `authorize` for every action, reads included:

| Route | `action` | `obj` | `data` |
|---|---|---|---|
| `GET /` | `"get_many"` | `None` | `None` |
| `GET /{id}` | `"get_one"` | the loaded row | `None` |
| `POST /` | `"create"` | `None` | the validated request body |
| `PATCH /{id}` | `"update"` | the loaded row | the validated request body |
| `DELETE /{id}` | `"delete"` | the loaded row | `None` |
| a `write_action` block | its name, such as `"publish"` | the `obj` it gets, or `None` | the `data` it gets |

For `get_one`, `update` and `delete`, the handler first loads the row through
the view's scope, and then calls `authorize` with it. So `authorize` only gets
rows that the caller can see. For `get_many`, it runs before the query and
gets no row. On a write, it runs before the change is made.
[A read](#read-request-steps) shows where `authorize` runs among the steps of
a request.

A refusal from `authorize` is a 403: the caller can see the row, but may not
do this. To hide a row, so that the caller gets a 404 as if the row did not
exist, use a [scope](scopes.md) instead.

Restly does not check who the caller is. Your app does that with FastAPI's
security tools, and binds the caller with
[Current & ContextParam](#current-request-binding).

(authorize-gate)=
## Allow or refuse an action

The blog example lets everyone read, lets every user create, and lets only the
author change a post or a comment:

```{literalinclude} ../example-projects/blog/blog/main.py
:language: python
:pyobject: AuthoredView
```

- A read returns at once. The scope already decides which posts each reader
  sees.
- A write needs a user. `Current.user_id()` is the caller, bound for each
  request ([Current & ContextParam](howto_current.md)). Without a user, the
  answer is a 401, from a plain `fastapi.HTTPException`.
- Every user may create.
- Every other action changes one row, and `obj` is that row. Only its author
  may change it. This rule also covers actions that you add later, such as
  `publish`: a new action is refused until you allow it.

`PostView` and `CommentView` both inherit from `AuthoredView`, so the rule is
written once. [Views](#share-behaviour-with-base-views) shows more ways to
share behavior between views. The full file is
[`blog/main.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/blog/blog/main.py).

A rule can also read the request body in `data`. On an update,
`data.model_fields_set` names the fields that the client sent. This view gets
the caller from a class attribute dependency, which works as well as
`Current`:

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
        if action in (fr.Action.GET_MANY, fr.Action.GET_ONE):
            return
        if not self.current_user.is_staff:
            raise fr.exc.Forbidden("Only staff can change invoices")
        if action == fr.Action.UPDATE and "amount" in data.model_fields_set:
            if not self.current_user.is_manager:
                raise fr.exc.Forbidden("Only a manager can change the amount")
```

[Declare dependencies as class attributes](#dependency-injection-on-class-attributes)
explains `current_user`. A rule that needs the database can query
`self.session`. In the SaaS example, a member may change the labels of a task
only when the task is assigned to them
([`app/labels/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/labels/views.py)).

`authorize` is an `async def` on
{class}`AsyncRestView <fastapi_restly.views.AsyncRestView>` and a plain `def`
on {class}`RestView <fastapi_restly.views.RestView>`. To refuse, raise
{class}`fr.exc.Forbidden <fastapi_restly.exc.Forbidden>` or any other
`fastapi.HTTPException`. [Errors](howto_error_responses.md) lists the
exceptions that Restly provides.

(action-names)=
## Action names and `fr.Action`

`action` is a string. {class}`fr.Action <fastapi_restly.views.Action>` has a
constant for each CRUD action:

| Constant | Value |
|---|---|
| {attr}`fr.Action.GET_MANY <fastapi_restly.views.Action.GET_MANY>` | `"get_many"` |
| {attr}`fr.Action.GET_ONE <fastapi_restly.views.Action.GET_ONE>` | `"get_one"` |
| {attr}`fr.Action.CREATE <fastapi_restly.views.Action.CREATE>` | `"create"` |
| {attr}`fr.Action.UPDATE <fastapi_restly.views.Action.UPDATE>` | `"update"` |
| {attr}`fr.Action.DELETE <fastapi_restly.views.Action.DELETE>` | `"delete"` |

`fr.Action` is a class of string constants, not an `Enum`. The constants are
plain strings, so they compare equal to `"get_many"` and the other names. A
custom action can use any other name, such as `"publish"`. Prefer the
constants in your own code: a typo in a constant raises `AttributeError`, and
a type checker reports it, but a typo in a string only makes a rule never
match. Give your custom action names constants too, for the same reason.

The transaction hooks get the same name as their `action` argument:
{meth}`before_action_commit <fastapi_restly.views.RestView.before_action_commit>`
and
{meth}`after_action_commit <fastapi_restly.views.RestView.after_action_commit>`.
Reads do not call the hooks, so the hooks only see write actions. See
[Run code before or after the commit](#transaction-hooks).

(authorize-custom-actions)=
## Authorize a custom action

A custom route reaches `authorize` in one of these ways:

- A {meth}`write_action <fastapi_restly.views.RestView.write_action>` block
  calls it with the block's name: `write_action("publish", obj=post)` calls
  `authorize("publish", obj=post, data=None)`. If `authorize` raises, the
  block does not run.
- A handler that the route calls authorizes its CRUD action, also under
  another path. `handle_update(...)` calls `authorize("update", ...)`, and
  {meth}`handle_get_one <fastapi_restly.views.RestView.handle_get_one>` calls
  `authorize("get_one", ...)`.
- The route calls `self.authorize(...)` itself. A route that builds its own
  query does this, as the count route in
  [Use the scope in your own query](#reading-the-scope) does.

The business methods, such as
{meth}`get_one <fastapi_restly.views.RestView.get_one>`, never call
`authorize`. A custom route that only calls them checks nothing.

Write `authorize` so that it refuses an action name that it does not know, as
the blog example does. Then a new custom action stays closed until you allow
it. [Add a custom action route](#custom-action-route) shows the routes
themselves.

## See also

- [Scopes](scopes.md): which rows a caller can see, and the 404.
- [Request Lifecycle & Hooks](#view-request-steps): the steps of a request,
  and where `authorize` runs.
- [Current & ContextParam](howto_current.md): who is calling.
- [Errors](howto_error_responses.md): the exceptions to raise, and how errors
  look.
