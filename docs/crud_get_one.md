(get-one-route)=
# Get One

`GET /{id}` returns one row of the view's model. A read changes nothing, so
it has no business method and no commit.
[How a CRUD route runs](#view-request-steps) explains the methods of each
route. For `GET /{id}`, these steps run:

:::{call-tree}
- `GET /{id}`
  - {override}`get_one_endpoint(id) <fastapi_restly.views.RestView.get_one_endpoint>`
    The endpoint method.
    - {meth}`handle_get_one(id) <fastapi_restly.views.RestView.handle_get_one>`
      The handler. You call it, but you cannot override it.
      - {meth}`get_one(id) <fastapi_restly.views.RestView.get_one>`
        The load. You call it, but you cannot override it.
        - {meth}`select() <fastapi_restly.views.BaseRestView.select>`
          The model, the scope and the loader options.
        - {override}`where_id(id) <fastapi_restly.views.BaseRestView.where_id>`
          The WHERE that picks the row: the primary key by default.
        - {override}`not_found(id) <fastapi_restly.views.BaseRestView.not_found>`
          The error when no row matches: a 404 by default.
      - {override}`authorize("get_one", obj=obj) <fastapi_restly.views.RestView.authorize>`
        A refusal is a 403.
    - {override}`to_single_response(obj) <fastapi_restly.views.BaseRestView.to_single_response>`
      The response.
:::

A method marked *override* is a method that you can override in your view.

(get-one-what-to-change)=
## What you can change

The table starts with rules on the view and on the schema. They need less
code than an override, and they also hold for the other routes.

| Your change | Where it goes |
|---|---|
| The rows a client can see | [`scope`](#view-scope) |
| Who may read a row | [`authorize`](#authorize-gate) |
| How the route finds the row | [`where_id`](#natural-key-route) |
| The error for a missing row | [`not_found`](#natural-key-route) |
| The fields of the response | [The response schema](#own-response-schema) |
| The relationships it loads | [`get_loader_options`](#eager-load-extra-relationships) |
| A value in the response | [`to_single_response`](#value-on-each-row) |
| Status code, path, dependencies | [`fr.modify_route`](#modify-route) |
| Parameters, headers, another source | [`get_one_endpoint`](#replace-get-one-endpoint) |
| No get-one route | [`get_one_endpoint = None`](#leave-out-routes) |
| The same read on another path | [`handle_get_one`](#get-one-from-custom-route) |

(read-request-steps)=
## Visibility, then policy

A read decides in two steps whether the client gets the row:

- **Visibility.** The load starts from
  {meth}`select() <fastapi_restly.views.BaseRestView.select>`: the model with
  the view's scope. A row outside the scope is a 404 for every caller, as if
  the row did not exist. See [Scopes](scopes.md).
- **Policy.** The handler calls `authorize` with the loaded row. A refusal
  is a 403. See [Authorization](authorization.md).

`PATCH /{id}`, `DELETE /{id}` and your action routes load their row with the
same `get_one`. So the scope,
{meth}`where_id <fastapi_restly.views.BaseRestView.where_id>` and
{meth}`not_found <fastapi_restly.views.BaseRestView.not_found>` apply to them
too.

:::{admonition} Details
:class: details

You cannot override the loads: a view class that defines `get_one`,
`get_many` or `select` fails when Python creates the class. The message names
the place for each change. An override of a load could leave out the scope,
or the `scope=` that a route asked for, and nothing would tell you. Because
you cannot override the loads, the scope always applies.

`select()` also adds the options of
{meth}`get_loader_options <fastapi_restly.views.BaseRestView.get_loader_options>`,
so the rows are ready for the response. A custom read starts from it too, as
in `self.select().where(Item.price > 10)`. In a count, a subquery or a select
of some columns, SQLAlchemy leaves the loader options out.
:::

(natural-key-route)=
## Find the row by another key

By default, the item routes find the row by its primary key. To find it by
another column, such as a slug, override
{meth}`where_id <fastapi_restly.views.BaseRestView.where_id>`. It gets the id
from the path and returns the WHERE that picks the row. Set
{attr}`id_type <fastapi_restly.views.BaseRestView.id_type>` to the type of
the value in the path:

```python
from sqlalchemy import ColumnElement


@fr.include_view(app)
class RecipeView(fr.AsyncRestView):
    prefix = "/recipes"
    model = Recipe
    schema = RecipeSchema
    id_type = str

    def where_id(self, id: str) -> ColumnElement[bool]:
        return Recipe.slug == id
```

Now `GET`, `PATCH` and `DELETE /recipes/{slug}` find the recipe by its slug,
and so does `self.get_one(slug)` in your own routes. To change the error for
a row that does not exist, override
{meth}`not_found <fastapi_restly.views.BaseRestView.not_found>`. It returns
the exception that `get_one` raises:

```python
    def not_found(self, id: str | None) -> Exception:
        return fr.exc.NotFound(f"No recipe has the slug {id!r}")
```

To find a row by another key in one route only, pass an expression to the
handler; see [Read a row by another key](#custom-route-other-key).

:::{admonition} Details
:class: details

`where_id` is a plain `def`, also on an async view: it builds SQL and runs
no query. `not_found` may be `async def` on an async view, and Restly awaits
it. When a route loads by an expression, `not_found` gets `None`, because the
404 body is no place for SQL. A hook that returns the wrong kind of value
raises `TypeError`.

Combine columns with `sqlalchemy.and_(...)`. This is how a route reaches a
row of a model with a composite primary key. A plain id cannot name such a
row, so a view of such a model must override `where_id` to read the parts
from the path, such as `"3-7"`. Set `id_type = str` too: without it, the
path takes only an integer, and `/seats/3-7` is a 404 before `where_id`
runs. Then the default item routes work for the model:

```python
class SeatView(fr.AsyncRestView):
    prefix = "/seats"
    model = Seat
    id_type = str  # the id in the path is "3-7"

    def where_id(self, id: str) -> ColumnElement[bool]:
        row, _, number = id.partition("-")
        if not (row.isdigit() and number.isdigit()):
            raise self.not_found(id)
        return sqlalchemy.and_(Seat.row == int(row), Seat.number == int(number))
```

A view that keeps the default `where_id` fails when it registers, unless it
leaves the item routes out: `get_one_endpoint = None`, `update_endpoint = None`
and `delete_endpoint = None`.

The WHERE narrows the read inside the scope. It never reaches past it, so a
row that the view hides stays a 404 under any key. Only
{attr}`scope <fastapi_restly.views.BaseRestView.scope>` changes what the view
sees. When more than one row matches, Restly raises SQLAlchemy's
`MultipleResultsFound` and does not return the first row: a key that matches
several rows is a bug in the expression.

The expression that `where_id` returns compares columns of the view's model.
A column of another table raises `TypeError`, because SQL would join every
row of that table and could pick a wrong row. For a condition on a related
row, use `.has()` or `.any()`, such as `Book.shelf.has(Shelf.code == code)`.
:::

(replace-get-one-endpoint)=
## Replace `get_one_endpoint`

To change one option of the route, such as its dependencies, use
{func}`fr.modify_route <fastapi_restly.views.modify_route>`. When the change
needs code, define `get_one_endpoint` in your view, with a route decorator.
Keep the call to `handle_get_one`, so the scope and `authorize` still run.
This view lets clients keep the row for a minute:

```python
from fastapi import Response
from fastapi_restly.views import IDType, ResponseSchema


@fr.include_view(app)
class ProductView(fr.AsyncRestView):
    prefix = "/products"
    model = Product

    @fr.get("/{id}")
    async def get_one_endpoint(self, id: IDType, response: Response) -> ResponseSchema:
        response.headers["Cache-Control"] = "max-age=60"
        return await self.handle_get_one(id)
```

`response: Response` is FastAPI's way to set a header on the response; see
[Response Headers](https://fastapi.tiangolo.com/advanced/response-headers/).
`IDType` and `ResponseSchema` stand for the id type and the response schema
of the view; see
[Name the view's schemas in a custom route](#name-the-view-schemas).
[Replace an endpoint method](#replace-endpoint-method) has the rules for all
five routes.

A read that does not come from the database, such as one from a cache or
another service, replaces the endpoint method in the same way. It does not
call `handle_get_one`, so it calls `authorize` itself.

(get-one-from-custom-route)=
## Read a row from a custom route

{meth}`handle_get_one <fastapi_restly.views.RestView.handle_get_one>` runs
all the steps of `GET /{id}`. Call it from a route of your own to read a row
under another path. It takes the id of the row, or a SQLAlchemy expression
that picks the row:

```python
    @fr.get("/me")
    async def get_me(self) -> ResponseSchema:
        return await self.handle_get_one(Current.user_id())
```

`Current.user_id()` is the id of the caller; see
[Request Context](context.md). The scope and `authorize` run as on
`GET /users/{id}`. The route `/me` works beside `/{id}`, because Restly adds
the routes of the view before the CRUD routes.
[Add a custom read route](#custom-read-route) has more examples, such as a
read that starts from `self.select()`.
