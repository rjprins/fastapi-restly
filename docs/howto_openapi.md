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

To change a default, set a view attribute or a route option. To document a
custom route, pass keyword arguments to its decorator, as in FastAPI.

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
Restly's IDs. To keep an existing operation ID, set `operation_id` in the
route's options; see the next section.

(route-options)=
## Change the name, summary or operation ID of a CRUD route

Set {attr}`route_options <fastapi_restly.views.View.route_options>` to pass
FastAPI route keyword arguments to an endpoint method, without replacing the
method:

```python
class InvoiceView(fr.AsyncRestView):
    prefix = "/invoices"
    model = Invoice
    schema = InvoiceSchema
    route_options = {
        fr.ViewRoute.GET_MANY: {
            "name": "list_invoices",
            "summary": "List invoices",
            "operation_id": "billing_list_invoices",
        },
        "create_endpoint": {"summary": "Create an invoice"},
    }
```

A key is an endpoint method name or a
{class}`ViewRoute <fastapi_restly.views.ViewRoute>` value. Use `"put"` for the
PUT route of React Admin, and the method name for a custom route. A value
takes the same keyword arguments as a route decorator, except the path. An
unknown method name raises
{class}`RestlyConfigurationError <fastapi_restly.exc.RestlyConfigurationError>`
when the view registers. Options for an excluded route are ignored.

The options win over the keyword arguments of the decorator, which win over
Restly's defaults. A subclass inherits the mapping. A subclass that declares
its own mapping replaces the parent's: Restly does not merge the two.

The route names repeat across views. If you use `app.url_path_for()` to build
the URLs of routes in several views, give them names of their own. Each
explicit `operation_id` must be unique in the app's spec.

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

A field gets it only when a view for the target model is included on the same
app. A nested object, such as `author: AuthorSchema`, gets none. The
reference types themselves are on
[Relationships](howto_relationship_idschema.md).

There is a known limit: views that you include on an `APIRouter`, not on the
app, do not get these annotations. The check for two classes with the same
name still covers them when you pass the app to
{func}`fr.configure(app, ...) <fastapi_restly.db.configure>`.

## See also

- [Views](views.md): the class attributes that every view has.
- [Endpoint decorators](#endpoint-decorators): the decorators and the
  keyword arguments that they pass to FastAPI.
- [Schemas & Validation](schemas.md): the classes that the spec
  shows, and their names.
