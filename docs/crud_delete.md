(delete-route)=
# Delete

`DELETE /{id}` removes one row of the view's model. The response is empty,
with status 204. [How a CRUD route runs](#view-request-steps) explains the
three methods of each route: the endpoint method, the handler and the
business method. For `DELETE /{id}`, these steps run:

:::{call-tree}
- `DELETE /{id}`
  - {override}`delete_endpoint(id) <fastapi_restly.views.RestView.delete_endpoint>`
    The endpoint method. The response is empty, with status 204.
    - {meth}`handle_delete(id) <fastapi_restly.views.RestView.handle_delete>`
      The handler. You call it, but you cannot override it.
      - {meth}`get_one(id) <fastapi_restly.views.RestView.get_one>`
        Loads the row through the scope. A missing or hidden row is a 404.
      - {override}`authorize("delete", obj=obj) <fastapi_restly.views.RestView.authorize>`
        A refusal is a 403.
      - {override}`snapshot(obj) <fastapi_restly.views.BaseRestView.snapshot>`
        Takes `old`: the column values before the delete.
      - {override}`delete(obj) <fastapi_restly.views.RestView.delete>`
        The business method: it marks the row for deletion.
      - Save: flush. The `DELETE` statement runs here.
      - {override}`before_action_commit("delete", new=None, old=old) <fastapi_restly.views.RestView.before_action_commit>`
      - Commit.
      - {override}`after_action_commit("delete", new=None, old=old) <fastapi_restly.views.RestView.after_action_commit>`
:::

A method marked *override* is a method that you can override in your view.

(delete-what-to-change)=
## What you can change

The table starts with rules on the view. They need less code than an
override, and they also hold for the other routes.

| Your change | Where it goes |
|---|---|
| The rows a client can delete | [`scope`](#view-scope) |
| Who may delete | [`authorize`](#authorize-gate) |
| How the route finds the row | [`where_id`](#natural-key-route) |
| What the delete does | [`delete`](#delete-method) |
| Keep the row: a soft delete | [`delete`](#soft-delete-recipe) |
| Work before the commit | [`before_action_commit`](#transaction-hooks) |
| Work after the commit | [`after_action_commit`](#transaction-hooks) |
| Status code, path, dependencies | [`fr.modify_route`](#modify-route) |
| Send the deleted row back | [`delete_endpoint`](#replace-delete-endpoint) |
| No delete route | [`delete_endpoint = None`](#leave-out-routes) |
| The same delete on another path | [`handle_delete`](#delete-from-custom-route) |

(delete-method)=
## Change what the delete does

Override {meth}`delete <fastapi_restly.views.RestView.delete>`. It gets the
row. Call `super().delete()` to mark the row for deletion. This view refuses
to delete an invoice that is paid:

```python
@fr.include_view(app)
class InvoiceView(fr.AsyncRestView):
    prefix = "/invoices"
    model = Invoice

    async def delete(self, obj):
        if obj.paid:
            raise fr.exc.Conflict("A paid invoice cannot be deleted")
        await super().delete(obj)
```

When `delete` runs, you can rely on these things:

- `obj` is the row. Restly loaded it through the view's scope, and
  `authorize` allowed the delete.
- Nothing is written to the database yet.
- You can use `self.session`, `self.request` and the view's dependencies, as
  in every method of the view.

The default `delete` calls `session.delete(obj)`. It marks the row for
deletion, and the `DELETE` statement runs at the save. `delete` returns
nothing. After it, the handler saves, runs `before_action_commit`, commits
and runs `after_action_commit`. When `delete` raises, nothing is deleted, and
the client gets the error. [Errors](errors.md) lists the exceptions that you
can raise.

To keep the row and set a timestamp, do not call `super().delete()`; see
[Make `DELETE` set a timestamp](#soft-delete-recipe). The hooks then get the
row as `new`, because it still exists.

:::{admonition} Details
:class: details

A delete that must finish outside the request can mark the row in `delete`,
as a soft delete does. After the commit, `after_action_commit` starts the
real delete:

```python
    async def delete(self, obj):
        obj.status = "pending_deletion"  # no super(): the row stays

    async def after_action_commit(self, action, new, old=None):
        if action == self.Action.DELETE:
            await enqueue_async_delete(new.id)  # a worker deletes the row
```

`delete` runs for `DELETE /{id}`, for a custom route that calls
`handle_delete`, and in a `write_action` block that calls `self.delete()`.
:::

(replace-delete-endpoint)=
## Replace `delete_endpoint`

To change one option of the route, such as its status code, use
{func}`fr.modify_route <fastapi_restly.views.modify_route>`. When the change
needs code, define `delete_endpoint` in your view, with a route decorator.
Keep the call to `handle_delete`, so the load, `authorize`, your `delete`
and the commit still run. This view answers `200` with the deleted row:

```python
from fastapi_restly.views import IDType, ResponseSchema


@fr.include_view(app)
class ProductView(fr.AsyncRestView):
    prefix = "/products"
    model = Product

    @fr.delete("/{id}", status_code=200)
    async def delete_endpoint(self, id: IDType) -> ResponseSchema:
        obj = await self.get_one(id)               # load through the scope, or 404
        response = self.to_single_response(obj)    # the row as GET /{id} sends it
        await self.handle_delete(id)               # authorize, delete, commit
        return response
```

React Admin's `ra-data-simple-rest` expects this response
([React Admin](react_admin.md)). The route builds the response before the
row is gone. `ResponseSchema` makes the response schema the `response_model`,
so only its fields go out, as from `GET /{id}`, and OpenAPI shows them.
[Replace an endpoint method](#replace-endpoint-method) has the rules for all
five routes.

(delete-from-custom-route)=
## Delete a row from a custom route

{meth}`handle_delete <fastapi_restly.views.RestView.handle_delete>` runs all
the steps of `DELETE /{id}`. Call it from a route of your own to delete a row
under another path. It takes the id of the row, or a SQLAlchemy expression
that picks the row:

```python
    @fr.delete("/by-email/{email}")
    async def delete_by_email(self, email: str) -> None:
        await self.handle_delete(User.email == email)
```

The scope, `authorize`, your `delete` and the commit run as on
`DELETE /users/{id}`. `fr.delete` answers `204`, as the default route does.
