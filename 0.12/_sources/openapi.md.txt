# OpenAPI

Restly adds each route of a view to FastAPI as a normal path operation. So
FastAPI builds the OpenAPI spec, and FastAPI's options work on every route,
the CRUD routes included. Restly fills in a few things for you:

- the tag of each route: the view's `tags`, or else a tag made from the last
  segment of the prefix, so `/order-items` gives `Order Items`;
- the name, the summary and the operation ID of each CRUD route;
- the request and response types of the CRUD routes: the classes that Restly
  builds from the view's schema, named as in
  [The names of the generated classes](#generated-class-names);
- the filter, sort and page parameters of each list route;
- an `x-resource-ref` on each field that points to another resource.

When the app builds its spec, Restly also warns when two classes have the same
name; see [Name your schemas](#name-your-schemas).

To change a default, set a view attribute, or override the endpoint method
with a decorator of your own. To document a custom route, pass keyword
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
entry for the same status code in a subclass wins. A CRUD view starts with
`{404: {"description": "Not found"}}`.

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
out the final `{id}` parameter and the HTTP method. So a view mounted under
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

The route name is the action part of the ID. So `name="search"` on the list
route gives `items_search`. Custom routes, and the routes of a plain
{class}`View <fastapi_restly.views.View>`, get FastAPI's normal operation IDs.

An explicit `operation_id` always wins. You can also give FastAPI a
[`generate_unique_id_function`](https://fastapi.tiangolo.com/advanced/generate-clients/#custom-generate-unique-id-function)
on a route, a router, an `include_router()` call or the app. It replaces
Restly's IDs, with FastAPI's normal order of precedence. It receives the route
names above, so a function based on the tag and the name can give IDs such as
`items-get_many`.

If you generate clients from the spec, generate them again when you move to
Restly's IDs. To keep an existing operation ID, pass `operation_id` to the
decorator of the endpoint method; see the next section.
:::

(crud-route-metadata)=
## Change the name, summary or operation ID of a CRUD route

Override the endpoint method, and pass the keyword arguments to its
decorator, as in FastAPI. The method calls the method of the base class, so
the route works as before:

```python
from fastapi_restly.views import ViewListResponse


class InvoiceView(fr.AsyncRestView):
    prefix = "/invoices"
    model = Invoice
    schema = InvoiceSchema

    @fr.get("/", summary="List invoices", operation_id="billing_list_invoices")
    async def get_many_endpoint(self, list_params) -> ViewListResponse:
        return await super().get_many_endpoint(list_params)
```

`ViewListResponse` keeps the view's list response as the response model; see
[Name the view's classes in a custom route](#name-the-response-classes).
Restly still fills in the rest of the route: the list params, and the name
and the summary that you do not pass. A keyword argument
that you pass wins over Restly's default. The table in
[The names and operation IDs of the CRUD routes](#default-route-metadata)
lists the endpoint methods.

:::{admonition} Details
:class: details

The decorator passes its keyword arguments to FastAPI, so every option of a
FastAPI route works. For example, `dependencies` adds a check to one route:

```python
    @fr.post("/", dependencies=[Depends(require_admin)])
    async def create_endpoint(self, data: ViewCreate) -> ViewResponse:
        return await super().create_endpoint(data)
```

An item route that you declare, such as `GET /{id}`, keeps the path that you
give it. Restly adds the `int` or `uuid` converter only to a path parameter
annotated `ViewID`; see [Item paths and static routes](#item-route-matching).

A subclass inherits the method and its decorator, so it also inherits an
explicit `operation_id`. Each `operation_id` must be unique in the app's
spec.

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
    async def publish(self, id: int): ...
```

[Endpoint decorators](#endpoint-decorators) lists the decorators and their
default status codes.

## Document another response for a CRUD route

The `response_model` of a CRUD route, and so its schema in the spec, comes
from the classes that Restly builds from the view's
{attr}`schema <fastapi_restly.views.BaseRestView.schema>`, such as
`UserResponse` and `UserCreate`. To change the fields of every route, set
the view's response class; see
[Set your own response class](#own-response-class). To document and return
another shape on one route, replace its endpoint method with your own
decorator and `response_model`; see
[Change the shape of one route](#one-route-shape) and
[Use a different schema for the list route](#list-schema).

A route that the view leaves out with
{attr}`include_routes <fastapi_restly.views.BaseRestView.include_routes>` or
{attr}`exclude_routes <fastapi_restly.views.BaseRestView.exclude_routes>` is
not in the spec at all; see [Exclude CRUD routes](#exclude-crud-routes).

(x-resource-ref)=
## See which resource an id points to

Restly adds the vendor extension `x-resource-ref` to each field that points to
another resource. A client or a code generator can read it. Its value is the
prefix of the view that serves the target model, without the leading slash:

```json
{
  "author_id": {"type": "integer", "title": "Author Id", "x-resource-ref": "authors"}
}
```

Two kinds of fields get it:

- a field named after a foreign key column, whatever its type, such as
  `author_id: fr.MustExist[int, Author]` or `author_id: int`;
- a field named after a relationship, with the type
  {class}`fr.IDRef[Model] <fastapi_restly.schemas.IDRef>` or
  {class}`fr.IDSchema[Model] <fastapi_restly.schemas.IDSchema>`, or a list of
  them.

:::{admonition} Details
:class: details

A field gets it only when a view for the target model is included on the same
app. A nested object, such as `author: AuthorSchema`, gets none. The
reference types themselves are on
[Relationships](relationships.md).

There is a known limit: views that you include on an `APIRouter`, not on the
app, do not get these annotations. The check for two classes with the same
name still covers them when you pass the app to
{func}`fr.configure(app, ...) <fastapi_restly.db.configure>`.

Restly adds the annotations in the app's `app.openapi()`, and checks the
class names there too. FastAPI's function `get_openapi()` does neither. So to
write the spec to a file, call `app.openapi()`. Restly wraps `app.openapi`
when the first view is included on the app, or when you pass the app to
`fr.configure`. An `app.openapi` of your own that you set after that replaces
Restly's. Set it before, and Restly wraps it.
:::

## See also

- [Views](views.md): the class attributes that every view has.
- [Endpoint decorators](#endpoint-decorators): the decorators and the
  keyword arguments that they pass to FastAPI.
- [Schemas & Validation](schemas.md): the classes that the spec
  shows, and their names.
