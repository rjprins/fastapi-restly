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
| a `write_action` block | its name, such as `"publish"` | the `obj` it gets when that is a row from the database; `None` for a new object or no object | the `data` it gets |

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
[Request Context](#current-request-binding).

So a rule goes in one of two places. A rule about who the caller is, such as
"the caller is logged in" or "the caller is an admin", goes in a FastAPI
dependency: on the app, on a router, or in the view's
{attr}`dependencies <fastapi_restly.views.View.dependencies>`. A rule that
depends on the action, the row or the request body goes in `authorize`. A
dependency runs for every route of the view, and before Restly loads the row,
so it cannot see the row.

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
  request ([Request Context](context.md)). Without a user, the
  answer is a 401, from a plain `fastapi.HTTPException`.
- Every user may create.
- Every other action changes one row, and `obj` is that row. Only its author
  may change it. This rule also covers actions that you add later, such as
  `publish`: only the author can run them, until you write another rule.

`PostView` and `CommentView` both inherit from `AuthoredView`, so the rule is
written once. [Views](#share-behaviour-with-base-views) shows more ways to
share behavior between views. The full file is
[`blog/main.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/blog/blog/main.py).

`authorize` is an `async def` on
{class}`AsyncRestView <fastapi_restly.views.AsyncRestView>` and a plain `def`
on {class}`RestView <fastapi_restly.views.RestView>`. To refuse, raise
{class}`fr.exc.Forbidden <fastapi_restly.exc.Forbidden>` or any other
`fastapi.HTTPException`. [Errors](errors.md) lists the
exceptions that Restly provides.

:::{admonition} Details
:class: details

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
:::

(action-names)=
## Action names and `fr.Action`

Each view has a class `Action` that lists all its actions.
{class}`fr.Action <fastapi_restly.views.Action>` has the five CRUD actions:

| Constant | Value |
|---|---|
| {attr}`fr.Action.GET_MANY <fastapi_restly.views.Action.GET_MANY>` | `"get_many"` |
| {attr}`fr.Action.GET_ONE <fastapi_restly.views.Action.GET_ONE>` | `"get_one"` |
| {attr}`fr.Action.CREATE <fastapi_restly.views.Action.CREATE>` | `"create"` |
| {attr}`fr.Action.UPDATE <fastapi_restly.views.Action.UPDATE>` | `"update"` |
| {attr}`fr.Action.DELETE <fastapi_restly.views.Action.DELETE>` | `"delete"` |

A view without actions of its own has this class. A view that adds an action
declares a subclass with the same name. The blog's `PostView` adds
`publish`:

```{literalinclude} ../example-projects/blog/blog/main.py
:language: python
:start-at: class PostView(AuthoredView):
:end-at: PUBLISH = "publish"
```

{meth}`write_action <fastapi_restly.views.RestView.write_action>` takes only a
name that the view's `Action` declares. Another name raises `ValueError`. So
the class shows every action that can reach `authorize` and the hooks. Use the
constants in your code, such as `self.Action.PUBLISH` in a route and in
`authorize`. Your editor completes them, and a type checker reports a typo.
The constants are plain strings, so `self.Action.PUBLISH == "publish"`.

A mixin or a base view with actions of its own declares its `Action` class in
the same way. A view that adds more actions inherits from the `Action` class
of each such base:

```python
class TrashMixin:
    class Action(fr.Action):
        RESTORE = "restore"


class PostView(TrashMixin, AuthoredView):
    class Action(TrashMixin.Action):
        PUBLISH = "publish"
```

Restly checks this when it creates the class. When the view's `Action` leaves
out an action of a base, it raises
{class}`RestlyConfigurationError <fastapi_restly.exc.RestlyConfigurationError>`
and names the base. This also catches a view without an `Action` of its own
and with two bases that each have one. Python would take the `Action` of the
first base and drop the other.

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
  calls it with the block's name: `write_action(self.Action.PUBLISH, obj=post)`
  calls `authorize("publish", obj=post, data=None)`. For a new object, such as the
  copy that a clone route creates, `authorize` gets `obj=None`, as on
  `POST /`; pass the request as `data=`, so a create rule sees what is
  created. If `authorize` raises, the block does not run.
- A handler that the route calls authorizes its CRUD action, also under
  another path. `handle_update(...)` calls `authorize("update", ...)`, and
  {meth}`handle_get_one <fastapi_restly.views.RestView.handle_get_one>` calls
  `authorize("get_one", ...)`.
- The route calls `self.authorize(...)` itself. A route that builds its own
  query does this, as the count route in
  [Use the scope in your own query](#reading-the-scope) does. This is a call
  to your own method, so Restly does not check the name. Use a name from the
  view's `Action` here too.

The loads and the business methods, such as
{meth}`get_one <fastapi_restly.views.RestView.get_one>` and
{meth}`update <fastapi_restly.views.RestView.update>`, never call
`authorize`. A custom route that only calls them checks nothing.

Write `authorize` so that an action name it does not know gets the strictest
rule, not no rule. The blog example does this: every action that is not a
read or a create needs the author. Then a new custom action is never open to
everyone. [Add a custom action route](#custom-action-route) shows the routes
themselves.

## See also

- [Scopes](scopes.md): which rows a caller can see, and the 404.
- [Request Lifecycle & Hooks](#view-request-steps): the steps of a request,
  and where `authorize` runs.
- [Request Context](context.md): who is calling.
- [Errors](errors.md): the exceptions to raise, and how errors
  look.
