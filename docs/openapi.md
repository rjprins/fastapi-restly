# OpenAPI

Restly adds each route of a view to FastAPI as a normal path operation. So
FastAPI builds the OpenAPI spec, and FastAPI's options work on every route,
the CRUD routes included. Restly fills in a few things for you:

- the tag of each route: the view's `tags`, or else a tag made from the last
  segment of the prefix, so `/order-items` gives `Order Items`;
- the name, the summary and the operation ID of each CRUD route;
- the request and response types of the CRUD routes: the schemas that Restly
  builds from the view's main schema, named as in
  [The names of the generated schemas](#generated-schema-names);
- the filter, sort and page parameters of each list route.

When the app builds its spec, Restly also warns when two classes have the same
name; see [Name your schemas](#name-your-schemas).

To change a default, set a view attribute. For one route, use
{func}`fr.modify_route <fastapi_restly.views.modify_route>`; see
[Change one option of a route](#modify-route). To document a custom route, pass keyword
arguments to its decorator, as in FastAPI.

(view-openapi-metadata)=
## Set tags and responses for a whole view

The class attributes
{attr}`tags <fastapi_restly.views.View.tags>` and
{attr}`responses <fastapi_restly.views.View.responses>` apply to every route
of the view, the CRUD routes and the custom routes:

```python
@fr.include_view(app)
class InvoiceView(fr.AsyncRestView):
    prefix = "/invoices"
    tags = ["billing"]
    responses = {402: {"description": "Payment required"}}
    model = Invoice
    schema = InvoiceSchema
```

`tags` replaces the tag that Restly makes from the prefix. A subclass
inherits it. `responses` adds to the responses of the base classes, and an
entry for the same status code in a subclass wins.

A route of a CRUD view documents `404` by itself when it can answer 404. That is
when its path has a parameter, or when its request body holds a reference to a
row. In both cases, the row can be missing. A custom route that answers 404 for
another reason documents it with `responses` on its decorator.

To leave a response out, set its status code to `None`. This works on a view,
such as `responses = {404: None}`, and on the decorator of one route.

The view's {attr}`dependencies <fastapi_restly.views.View.dependencies>` also
run on every route. A security dependency, such as an OAuth2 scheme, shows in
the spec as it does in FastAPI. [Views](#share-behaviour-with-base-views)
covers how a base view shares them.

(default-route-metadata)=
## The names and operation IDs of the CRUD routes

The CRUD routes get these FastAPI route names, operation IDs and summaries:

| Endpoint method | Route name | Operation ID at `/items` | Summary |
|---|---|---|---|
| `get_many_endpoint` | `get_many` | `items_list` | List |
| `get_one_endpoint` | `get_one` | `items_get` | Get |
| `create_endpoint` | `create` | `items_create` | Create |
| `update_endpoint` | `update` | `items_update` | Update |
| `delete_endpoint` | `delete` | `items_delete` | Delete |
| React Admin `put` | `put` | `items_put` | Update (PUT) |

An operation ID joins the full path of the resource and the action. It leaves
out the final path parameter and the HTTP method. So a view mounted under
`/v1` gets `v1_items_list` instead of `items_list`, and the same view mounted
under `/v1` and `/v2` gets different IDs. The class name and the tags of the
view do not change the IDs.

:::{admonition} Details
:class: details

A path parameter of a parent stays in the ID as `by_<parameter>`. For
`/projects/{project_id}/items`, the list ID is
`projects_by_project_id_items_list`. Punctuation becomes an underscore, so
`/order-items` gives `order_items_list`. Restly keeps the spelling of the
resource: `/people` gives `people_get`, not a singular form. Two paths that
give the same name, such as `/order-items` and `/order_items`, need explicit
operation IDs when they are in one spec.

The route name is the action part of the ID. A route that chooses its own
`name` does not get Restly's ID and summary. FastAPI makes them from the name,
as on any route. So `name="search"` on the list route gives the ID
`search_items_get` and the summary `Search`.

Custom routes, and the routes of a plain
{class}`View <fastapi_restly.views.View>`, also get FastAPI's normal IDs and
summaries, from the name of the method. So a method `stats` at `/items/stats`
gets the ID `stats_items_stats_get` and the summary `Stats`.

An explicit `operation_id` always wins. You can also give FastAPI a
[`generate_unique_id_function`](https://fastapi.tiangolo.com/advanced/generate-clients/#custom-generate-unique-id-function)
on a route, a router, an `include_router()` call or the app. It replaces
Restly's IDs, with FastAPI's normal order of precedence. It receives the route
names above, so a function based on the tag and the name can give IDs such as
`items-get_many`.

If you generate clients from the spec, generate them again when you move to
Restly's IDs. To keep an existing operation ID, set `operation_id` with
{func}`fr.modify_route <fastapi_restly.views.modify_route>`; see
[Change one option of a route](#modify-route).

A subclass inherits an explicit `operation_id` too. Each `operation_id` must be
unique in the app's spec.

The route names repeat across views. If you use `app.url_path_for()` to build
the URLs of routes in several views, give them names of their own.
:::

(document-custom-route)=
## Document a custom route

To document a custom route, pass keyword arguments to its route decorator.
The decorators pass them to FastAPI's `add_api_route()`, so a custom route
documents itself like any FastAPI route:

```python
    @fr.post(
        "/{id}/publish",
        status_code=200,
        summary="Publish an article",
        responses={409: {"description": "Already published"}},
    )
    async def publish(self, id: IDType): ...
```

[Endpoint decorators](#endpoint-decorators) lists the decorators and their
default status codes.

## Document another response for a CRUD route

The `response_model` of a CRUD route, and so its schema in the spec, comes
from the classes that Restly builds from the view's
{attr}`schema <fastapi_restly.views.BaseRestView.schema>`, such as
`UserResponse` and `UserCreate`. To change the fields of every route, set
the view's response schema; see
[Set your own response schema](#own-response-schema). To document and return
another shape on one route, replace its endpoint method with your own
decorator and `response_model`; see
[Change the shape of one route](#one-route-shape) and
[Use a different schema for the list route](#list-schema).

A route that the view leaves out with
{attr}`include_routes <fastapi_restly.views.BaseRestView.include_routes>` or
{attr}`exclude_routes <fastapi_restly.views.BaseRestView.exclude_routes>` is
not in the spec at all; see [Exclude CRUD routes](#exclude-crud-routes).

## See also

- [Views](views.md): the class attributes that every view has.
- [Endpoint decorators](#endpoint-decorators): the decorators and the
  keyword arguments that they pass to FastAPI.
- [Schemas & Validation](schemas.md): the classes that the spec
  shows, and their names.
