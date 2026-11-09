(create-route)=
# Create

`POST /` adds one row to the view's model. The client sends the fields of
the new row, and the response holds the saved row with its id.
[How a CRUD route runs](#view-request-steps) explains the three methods of
each route: the endpoint method, the handler and the business method. For
`POST /`, these steps run:

:::{call-tree}
- `POST /`
  - {override}`create_endpoint(data) <fastapi_restly.views.RestView.create_endpoint>`
    The endpoint method. FastAPI has checked `data` against the create schema.
    - {meth}`handle_create(data) <fastapi_restly.views.RestView.handle_create>`
      The handler. You call it, but you cannot override it.
      - {override}`authorize("create", data=data) <fastapi_restly.views.RestView.authorize>`
        A refusal is a 403.
      - Check the [references](#reference-exists) in the data. An id that does
        not exist is a 404.
      - {override}`create(data) <fastapi_restly.views.RestView.create>`
        The business method: it builds the object.
      - Save: add the object to the session, flush, and refresh it.
      - {override}`before_action_commit("create", new=obj, old=None) <fastapi_restly.views.RestView.before_action_commit>`
      - Commit.
      - {override}`after_action_commit("create", new=obj, old=None) <fastapi_restly.views.RestView.after_action_commit>`
    - {override}`to_single_response(obj) <fastapi_restly.views.BaseRestView.to_single_response>`
      The response, with status 201.
:::

A method marked *override* is a method that you can override in your view. Before
`create_endpoint` runs, FastAPI checks the request body against the view's
{attr}`create_schema <fastapi_restly.views.BaseRestView.create_schema>`. A
body that does not fit returns 422, and none of the steps run.

(create-what-to-change)=
## What you can change

The table starts with rules on the model and on the schema. They need less
code than an override, and they also hold for the other routes.

| Your change | Where it goes |
|---|---|
| The fields a client can send | [`fr.ReadOnly`](#readonly-and-writeonly) or [a create schema](#explicit-write-schemas) |
| Check one value | [A validator](#validation) |
| Who may create | [`authorize`](#authorize-gate) |
| A value that every write sets | [A column default](#write-path-values) |
| What the create does | [`create`](#create-method) |
| Work before the commit | [`before_action_commit`](#transaction-hooks) |
| Work after the commit | [`after_action_commit`](#transaction-hooks) |
| A value in the response | [`to_single_response`](#value-on-each-row) |
| Status code, path, dependencies | [`fr.modify_route`](#modify-route) |
| HTTP method, parameters, body | [`create_endpoint`](#replace-create-endpoint) |
| No create route | [`create_endpoint = None`](#leave-out-routes) |
| The same create on another path | [`handle_create`](#create-from-custom-route) |

(create-method)=
(password-hash-example)=
## Change what the create does

Override {meth}`create <fastapi_restly.views.RestView.create>`. It gets the
request body. It builds the new object and returns it. Call
`super().create()` to build the object from the fields that the client sent,
and change it after that call. This view stores a hash of the password, not
the password:

```python
import fastapi_restly as fr
from sqlalchemy.orm import Mapped

from .auth import hash_password
from .models import Base


class User(fr.IDMixin, Base):
    email: Mapped[str]
    password: Mapped[str]  # stores the hash. UserView.create writes it


class UserSchema(fr.IDSchema):
    email: str
    password: fr.WriteOnly[str]  # accepted on input, never in a response


@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserSchema

    async def create(self, data):
        user = await super().create(data)
        user.password = hash_password(data.password)
        return user
```

The default `create` passes each field that the client sent to the model's
constructor. So the plain password goes into `password` first, and the
override replaces it with the hash. The save comes after `create` returns,
so the database only gets the hash. `fr.WriteOnly` keeps the field out of
every response.

When `create` runs, you can rely on these things:

- `data` is the request body, an instance of the create schema. Pydantic has
  validated it.
- `authorize` allowed the create.
- Every row that `data` refers to by its id exists; see
  [Check that the referenced row exists](#reference-exists).
- Nothing is written to the database yet.
- You can use `self.session`, `self.request` and the view's dependencies, as
  in every method of the view.

The default `create` builds a new object of the view's model from the fields
that the client sent, except the `ReadOnly` fields, and adds it to the
session. It does not flush and does not commit. After `create` returns the
object, the handler saves it, runs `before_action_commit`, commits and runs
`after_action_commit`. When `create` raises, nothing is saved, and the client
gets the error. [Errors](errors.md) lists the exceptions that you can raise.

:::{admonition} Details
:class: details

`create` runs for `POST /`, and for a custom route that calls
`handle_create`. A script or a worker does not run it; see
[Run the same logic in a script or a worker](#logic-outside-a-view).

The generated update schema takes `password` too. Without more code,
`PATCH /users/{id}` stores a new password as plain text. So hash it in
`update` as well:

```python
    async def update(self, obj, data):
        user = await super().update(obj, data)
        if "password" in data.model_fields_set:
            user.password = hash_password(data.password)
        return user
```

The other way is an [update schema of your own](#explicit-write-schemas)
without `password`, and a separate route that changes the password. The SaaS
example does this: its change-password route checks the current password
first
([`app/users/views.py`](https://github.com/rjprins/fastapi-restly/blob/main/example-projects/saas/app/users/views.py)).

`create` must return the object. When it returns `None`, Restly raises
`TypeError`.
:::

(no-id-in-create)=
## The new object has no id in `create`

`create` returns the object before the save. So inside `create`, the new
object has no primary key yet. The values that the database or a mapper event
sets at the flush, such as a slug or a tenant id, are not there either. Code
that reads them gets `None`, not an error.

Most code that needs these values runs after the save. An outbox row goes in
[`before_action_commit`](#transaction-hooks), which gets the saved object.
When `create` itself needs the id, flush, as in plain SQLAlchemy:

```python
    async def create(self, data):
        report = await super().create(data)
        await self.session.flush()  # report.id is set now
        report.file_path = f"reports/{report.id}.pdf"
        return report
```

The flush writes the row, and the save writes the path with an `UPDATE`.

:::{admonition} Details
:class: details

- Nothing is written before the save, so a query inside `create` does not
  see the new object. A check such as "is this email taken?" after
  `super().create()` finds only the rows that are already in the database,
  as it should. Restly runs the business method under
  {attr}`session.no_autoflush <sqlalchemy.orm.Session.no_autoflush>` for
  this. Without it, a sync session would write the half-built object before
  the query.
- For the same reason, a query in a business method or a `write_action`
  block does not see any of your unflushed changes. A loop that looks a row
  up and adds it when it is missing, or a count of rows against a limit,
  must call `flush()` before the query.
- A `try` or a `with` around `super().create()` catches no error of the
  INSERT, because the INSERT runs at the save, after `create` returns. To
  catch it in `create`, flush inside the `try`.
- "Nothing is written" is about the objects in the session. A statement that
  you run yourself, such as
  `await self.session.execute(update(Product).values(...))`, goes to the
  database at once. It is part of the same transaction, so the commit
  of the action commits it, and an error rolls it back.
- Often you do not need the id at all. Link the objects with a
  relationship, such as `line.order = order`, and the flush copies the key.
:::

(create-build-yourself)=
## Build the object yourself

To build the object in another way, build it yourself and return it. You do
not need to add it to the session: the save does that.

```python
    async def create(self, data):
        # this model's constructor takes the values in another form
        return Product.from_catalog_entry(data.catalog_entry)
```

The handler has already checked the references in `data` (`fr.IDRef` and
`fr.MustExist`; see [Relationships](relationships.md)). So an object that you
build yourself keeps those rules.

(replace-create-endpoint)=
## Replace `create_endpoint`

To change one option of the route, such as its status code, use
{func}`fr.modify_route <fastapi_restly.views.modify_route>`. When the change
needs code, define `create_endpoint` in your view, with a route decorator.
Keep the call to `handle_create`, so `authorize`, your `create` and the
commit still run. This view also sends the URL of the new row in a
`Location` header:

```python
from fastapi import Response
from fastapi_restly.views import CreateSchema, ResponseSchema


@fr.include_view(app)
class ProductView(fr.AsyncRestView):
    prefix = "/products"
    model = Product

    @fr.post("/")
    async def create_endpoint(self, data: CreateSchema, response: Response) -> ResponseSchema:
        product = await self.handle_create(data)
        response.headers["Location"] = f"/products/{product.id}"
        return product
```

`response: Response` is FastAPI's way to set a header on the response; see
[Response Headers](https://fastapi.tiangolo.com/advanced/response-headers/).
`fr.post` answers `201`, as the default route does. `CreateSchema` and
`ResponseSchema` stand for the create schema and the response schema of the
view; see [Name the view's schemas in a custom route](#name-the-view-schemas).
[Replace an endpoint method](#replace-endpoint-method) has the rules for all
five routes.

(create-from-custom-route)=
## Create a row from a custom route

{meth}`handle_create <fastapi_restly.views.RestView.handle_create>` runs all
the steps of `POST /`. Call it from a route of your own to create a row under
another path, or from another request body:

```python
class SignUp(fr.BaseSchema):
    email: str
    password: str


@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserSchema

    @fr.post("/sign-up")
    async def sign_up(self, data: SignUp) -> ResponseSchema:
        return await self.handle_create(data)
```

`authorize`, your `create` and the commit run as on `POST /users`. So with
the `create` from [Change what the create does](#create-method), both routes
store a hash of the password.

To create several rows in one action, such as an order with its lines, see
[Add a custom action route](#custom-action-route).
