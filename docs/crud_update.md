(update-route)=
# Update

`PATCH /{id}` changes one row of the view's model. The client sends only the
fields that it wants to change, and the other fields keep their values.
[How a CRUD route runs](#view-request-steps) explains the three methods of
each route: the endpoint method, the handler and the business method. For
`PATCH /{id}`, these steps run:

```{code-block} text
:emphasize-lines: 2,5,8,10,12,13

PATCH /{id}
  └─ update_endpoint(id, data)              # endpoint method
       ├─ handle_update(id, data)           # handler (final)
       │    ├─ get_one(id)                  # loads the row through the scope, or 404
       │    ├─ authorize("update", obj=obj, data=data)   # or 403
       │    ├─ check the references in data # an id that does not exist: 404
       │    ├─ old = snapshot(obj)          # the column values before the change
       │    ├─ update(obj, data)            # business method: the change
       │    ├─ save                         # flush and refresh the row
       │    ├─ before_action_commit("update", new=obj, old=old)
       │    ├─ commit
       │    └─ after_action_commit("update", new=obj, old=old)
       └─ to_single_response(obj)           # the response
```

The highlighted lines are the methods that you can override. Before
`update_endpoint` runs, FastAPI checks the request body against the view's
{attr}`update_schema <fastapi_restly.views.BaseRestView.update_schema>`. A
body that does not fit returns 422, and none of the steps run.

(update-what-to-change)=
## What you can change

The table starts with rules on the model and on the schema. They need less
code than an override, and they also hold for the other routes.

| Your change | Where it goes |
|---|---|
| The fields a client can send | [`fr.ReadOnly`](#readonly-and-writeonly) or [an update schema](#explicit-write-schemas) |
| Check one value | [A validator](#validation) |
| The rows a client can update | [`scope`](#view-scope) |
| Who may update | [`authorize`](#authorize-gate) |
| Find the row by a slug | [`where_id`](#natural-key-route) |
| A value that every write sets | [`onupdate` on the column](#write-path-values) |
| Refuse a change, check two fields | [`update`](#update-method) |
| Write more rows in the same commit | [`before_action_commit`](#transaction-hooks) |
| Work after the commit | [`after_action_commit`](#transaction-hooks) |
| A value in the response | [`to_single_response`](#value-on-each-row) |
| Status code, path, dependencies | [`fr.modify_route`](#modify-route) |
| HTTP method, parameters, body | [`update_endpoint`](#replace-update-endpoint) |
| No update route | [`update_endpoint = None`](#leave-out-routes) |
| The same update on another path | [`handle_update`](#update-from-custom-route) |

(update-method)=
## Change what the update does

Override {meth}`update <fastapi_restly.views.RestView.update>`. It gets the
row and the request body. It changes the row and returns it. Call
`super().update()` to write the fields that the client sent:

```python
from fastapi import HTTPException


@fr.include_view(app)
class EventView(fr.AsyncRestView):
    prefix = "/events"
    model = Event

    async def update(self, obj, data):
        if obj.locked:
            raise fr.exc.Conflict("This event is locked")
        event = await super().update(obj, data)
        if event.ends_on < event.starts_on:
            raise HTTPException(422, "The event ends before it starts")
        return event
```

The first check reads the row as it is in the database. The second check
reads the row after the change. So it also works when the client sends only
one of the two dates. A Pydantic validator cannot do this check, because it
sees only the fields in the request body.

When `update` runs, you can rely on these things:

- `obj` is the row. Restly loaded it through the view's scope, and
  `authorize` allowed the update.
- `data` is the request body, an instance of the update schema. Pydantic has
  validated it. `data.model_fields_set` holds the names of the fields that
  the client sent.
- Every row that `data` refers to by its id exists; see
  [Check that the referenced row exists](#reference-exists).
- Nothing is written to the database yet.
- You can use `self.session`, `self.request` and the view's dependencies, as
  in every method of the view.

The default `update` writes each field that the client sent onto the row,
except the `ReadOnly` fields. It does not flush and does not commit. After
`update` returns the row, the handler saves it, runs `before_action_commit`,
commits and runs `after_action_commit`. When `update` raises, nothing is saved, and the client gets the
error. [Errors](errors.md) lists the exceptions that you can raise.

:::{admonition} Details
:class: details

`update` runs for `PATCH /{id}`, for a custom route that calls
`handle_update`, for the `PUT` route of a
[React Admin view](#react-admin-put), and in a `write_action` block that
calls `self.update()`. So a rule in `update` holds for all of them. A script or a
worker does not run it; see
[Run the same logic in a script or a worker](#logic-outside-a-view).

Nothing is written while `update` runs, so a query inside it sees the rows as
they are in the database, without your change. To make a query see the
change, call `await self.session.flush()` first.
[The new object has no id in `create`](#no-id-in-create) explains this for
`create`, and it is the same for `update`.

Restly takes the snapshot `old` before `update` runs. So
`before_action_commit` and `after_action_commit` can compare the values before
and after the change; see
[Run code before or after the commit](#transaction-hooks).

When a client sends `null` for a field whose type allows `None`, the column
becomes `NULL`. A field that the client leaves out keeps its value.

The generated update schema takes every field that the main schema does not
mark `ReadOnly`, also a `WriteOnly` field such as a password. To hash a new
password in `update`, see [Hash a password in `create`](#password-hash-example).

`update` must return the row. When it returns `None`, Restly raises
`TypeError`.
:::

(replace-update-endpoint)=
## Replace `update_endpoint`

To change one option of the route, such as its status code, use
{func}`fr.modify_route <fastapi_restly.views.modify_route>`. When the change
needs code, define `update_endpoint` in your view, with a route decorator.
Keep the call to `handle_update`, so the load, `authorize`, your `update` and
the commit still run. This view takes `PUT` with all fields, in place of
`PATCH`:

```python
from fastapi_restly.views import IDType, ResponseSchema


class ProductReplace(fr.BaseSchema):
    name: str
    price: float


@fr.include_view(app)
class ProductView(fr.AsyncRestView):
    prefix = "/products"
    model = Product

    @fr.put("/{id}")
    async def update_endpoint(self, id: IDType, data: ProductReplace) -> ResponseSchema:
        return await self.handle_update(id, data)
```

The view now has a `PUT /products/{id}` route, and no `PATCH` route. A body
without `name` or `price` returns 422. `IDType` and `ResponseSchema` stand
for the id type and the response schema of the view, so the path and the
response are the same as on the default route; see
[Name the view's schemas in a custom route](#name-the-view-schemas).

:::{admonition} Details
:class: details

To keep `PATCH` and add `PUT`, give the `PUT` route another method name, such
as `replace`. It is then a custom route next to the default one; see
[Update a row from a custom route](#update-from-custom-route).

A method named `update_endpoint` without a route decorator is not a route,
so the view then has no update route.
[Replace the endpoint method of one route](#replace-endpoint-method) has the
rules for all five routes.
:::

(update-from-custom-route)=
## Update a row from a custom route

{meth}`handle_update <fastapi_restly.views.RestView.handle_update>` runs all
the steps of `PATCH /{id}`. Call it from a route of your own to update a row
under another path. It takes the id of the row, or a SQLAlchemy expression
that picks the row:

```python
from fastapi_restly.views import ResponseSchema


class UpdateMeRequest(fr.BaseSchema):
    name: str | None = None
    email: str | None = None


@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User

    @fr.patch("/me")
    async def update_me(self, data: UpdateMeRequest) -> ResponseSchema:
        return await self.handle_update(Current.user_id(), data)

    @fr.patch("/by-email/{email}")
    async def update_by_email(self, email: str, data: UpdateMeRequest) -> ResponseSchema:
        return await self.handle_update(User.email == email, data)
```

`UpdateMeRequest` has only the fields `name` and `email`, so these routes
cannot change other fields. The scope, `authorize`, your `update` and the
commit run as on `PATCH /users/{id}`. `Current.user_id()` is the id of
the caller; see [Request Context](context.md).

A change that is more than new values for some fields, such as publishing a
post, is an action of its own; see
[Add a custom action route](#custom-action-route).
