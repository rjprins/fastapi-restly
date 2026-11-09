(list-route)=
# List

`GET /` returns a page of rows of the view's model. The client can filter,
sort and page the list with query parameters, the list params; see
[Filtering, Sorting & Pagination](filtering.md). A read changes nothing, so
it has no business method and no commit.
[How a CRUD route runs](#view-request-steps) explains the methods of each
route. For `GET /`, these steps run:

:::{call-tree}
- `GET /`
  - {override}`get_many_endpoint(list_params) <fastapi_restly.views.RestView.get_many_endpoint>`
    The endpoint method.
    - {meth}`handle_get_many(list_params) <fastapi_restly.views.RestView.handle_get_many>`
      The handler. You call it, but you cannot override it.
      - {override}`authorize("get_many") <fastapi_restly.views.RestView.authorize>`
        There is no row yet. A refusal is a 403.
      - {meth}`get_many(list_params) <fastapi_restly.views.RestView.get_many>`
        The load. You call it, but you cannot override it.
        - {meth}`select() <fastapi_restly.views.BaseRestView.select>`
          The model, the scope and the loader options.
        - {override}`apply_list_params(stmt, list_params) <fastapi_restly.views.RestView.apply_list_params>`
          The filters, the sort and the page.
        - {override}`count(stmt) <fastapi_restly.views.RestView.count>`
          The total, on a paginated view.
    - {override}`to_list_response(result) <fastapi_restly.views.BaseRestView.to_list_response>`
      The list response.
      - {override}`to_single_response(row) <fastapi_restly.views.BaseRestView.to_single_response>`
        The response of each row.
:::

A method marked *override* is a method that you can override in your view.
[Visibility, then policy](#read-request-steps) explains the scope and
`authorize` of a read.

(list-what-to-change)=
## What you can change

The table starts with settings on the view. They need less code than an
override.

| Your change | Where it goes |
|---|---|
| The rows a client can see | [`scope`](#view-scope) |
| Who may list | [`authorize`](#authorize-gate) |
| The filter and sort keys | [`include_filters`](#choose-list-keys) |
| A filter of your own | [`fr.list_filter`](#own-list-filter) |
| The size and style of a page | [`pagination`](#list-pagination) |
| How the list params apply | [`apply_list_params`](#own-list-params) |
| The fields of each row | [`list_response_schema`](#list-schema) |
| The envelope of the list | [`pagination`](#change-list-envelope) |
| The relationships it loads | [`get_loader_options`](#eager-load-extra-relationships) |
| A value on each row | [`to_single_response`](#value-on-each-row) |
| Status code, path, dependencies | [`fr.modify_route`](#modify-route) |
| Parameters, headers, body | [`get_many_endpoint`](#replace-get-many-endpoint) |
| No list route | [`get_many_endpoint = None`](#leave-out-routes) |
| A subset on another path | [`handle_get_many`](#list-from-custom-route) |

:::{admonition} Details
:class: details

{meth}`count <fastapi_restly.views.RestView.count>` gives the total of a
paginated list. It counts the distinct primary keys of the rows that the
filters let through. Override it for a faster count on a large table, such
as an estimate.
:::

(replace-get-many-endpoint)=
## Replace `get_many_endpoint`

To change one option of the route, such as its dependencies, use
{func}`fr.modify_route <fastapi_restly.views.modify_route>`. When the change
needs code, define `get_many_endpoint` in your view, with a route decorator.
Keep the call to `handle_get_many`, so the scope, `authorize` and the list
params still apply. This view sends a plain list, with the total in a
header:

```python
from fastapi import Response
from fastapi_restly.views import ViewResponse


@fr.include_view(app)
class ProductView(fr.AsyncRestView):
    prefix = "/products"
    model = Product

    @fr.get("/")
    async def get_many_endpoint(self, list_params, response: Response) -> list[ViewResponse]:
        result = await self.handle_get_many(list_params)
        response.headers["X-Total-Count"] = str(result.total_count)
        return result.objects
```

The `list_params` parameter keeps Restly's filter, sort and page parameters.
The route declares a list of `ViewResponse`, the view's response schema,
so Restly passes each row through `to_single_response`, and OpenAPI shows
the list; see [The response of a custom route](#custom-route-response).
`response: Response` is FastAPI's way to set a header on the response; see
[Response Headers](https://fastapi.tiangolo.com/advanced/response-headers/).
[Replace an endpoint method](#replace-endpoint-method) has the rules for all
five routes.

:::{admonition} Details
:class: details

`list_params` needs no annotation: it always gets the view's
{attr}`list_params_schema <fastapi_restly.views.BaseRestView.list_params_schema>`.

A value from another service, or from a cache, can be added to all rows of
the page with one call. The replacement calls the handler, gets the values
for the whole page, and builds the response:

```python
from fastapi_restly.views import ViewListResponse


@fr.get("/")
async def get_many_endpoint(self, list_params) -> ViewListResponse:
    result = await self.handle_get_many(list_params)
    ratings = await rating_service.for_ids([p.id for p in result.objects])
    for project in result.objects:
        project.rating = ratings.get(project.id)
    return result
```
:::

(list-from-custom-route)=
## List rows from a custom route

{meth}`handle_get_many <fastapi_restly.views.RestView.handle_get_many>` runs
all the steps of `GET /`. Call it from a route of your own to list rows
under another path. `where=` narrows the list inside the scope:

```python
from fastapi_restly.views import ViewListResponse


@fr.include_view(app)
class TaskView(fr.AsyncRestView):
    prefix = "/tasks"
    model = Task

    @fr.get("/mine")
    async def mine(self, list_params) -> ViewListResponse:
        return await self.handle_get_many(
            list_params, where=Task.owner_id == Current.user_id()
        )
```

`GET /tasks/mine` takes the same filter, sort and page parameters as
`GET /tasks`, and returns the same envelope. `Current.user_id()` is the id of
the caller; see [Request Context](context.md).
[List a subset inside the scope](#per-read-where) explains `where=`, and
[Use the filters in a route of your own](#filters-in-own-route) shows a route
that builds its own query.
