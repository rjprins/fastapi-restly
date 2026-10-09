# Customize the OpenAPI Schema

Restly registers its default CRUD routes as ordinary FastAPI path operations,
so FastAPI's OpenAPI customization applies to them. This page maps the
customization points.

## Per-view metadata

To apply OpenAPI metadata across a whole view, set the
{attr}`tags <fastapi_restly.views.View.tags>`, {attr}`responses <fastapi_restly.views.View.responses>`, and {attr}`dependencies <fastapi_restly.views.View.dependencies>` class attributes.
They exist on every view and apply to all of its routes, default and custom:

```python
@fr.include_view(app)
class InvoiceView(fr.AsyncRestView):
    prefix = "/invoices"
    tags = ["billing"]
    responses = {402: {"description": "Payment required"}}
    model = Invoice
    schema = InvoiceSchema
```

(default-route-metadata)=
## Default route names and operation IDs

CRUD routes use these FastAPI route names and OpenAPI summaries:

| Endpoint method | Route name | Operation ID at `/items` | Summary |
|---|---|---|---|
| `get_many_endpoint` | `get_many` | `items_list` | List |
| `get_one_endpoint` | `get_one` | `items_get` | Get |
| `create_endpoint` | `create` | `items_create` | Create |
| `update_endpoint` | `update` | `items_update` | Update |
| `delete_endpoint` | `delete` | `items_delete` | Delete |
| React Admin `put` | `put` | `items_put` | Update (PUT) |

Default CRUD operation IDs combine the full resource path and the action.
They omit the final member parameter and the HTTP method suffix. Mounting a
view under `/v1` changes `items_list` to `v1_items_list`, so mounting the same
view under `/v1` and `/v2` gives distinct IDs. Renaming the view class or
changing its tags does not change these IDs.

Parent path parameters remain in the resource name as `by_<parameter>`.
For `/projects/{project_id}/items`, the list ID is
`projects_by_project_id_items_list`. Punctuation becomes underscores, so
`/order-items` uses `order_items_list`. Resource spelling is preserved:
`/people` uses `people_get`, without singularizing it. Paths that normalize
to the same name, such as `/order-items` and `/order_items`, need explicit
operation IDs when included in one schema.

Changing a CRUD route's `name` changes the action part of its generated ID.
For example, `name="search"` on the list route produces `items_search`.
Custom routes and plain {class}`View <fastapi_restly.views.View>` routes use
FastAPI's normal operation ID rules.

Explicit `operation_id` values take precedence. Applications can also supply
FastAPI's
[`generate_unique_id_function`](https://fastapi.tiangolo.com/advanced/generate-clients/#custom-generate-unique-id-function).
Generators supplied on a route, router, router inclusion, or app replace the
CRUD defaults using FastAPI's normal precedence. They receive the route names
above, so a generator based on tag and name can produce IDs such as
`items-get_many`.

Regenerate clients when adopting these defaults. To preserve an existing
operation ID, set `operation_id` in the route's options.

## Per-route metadata without replacing a method

Set {attr}`route_options <fastapi_restly.views.View.route_options>` to pass
FastAPI route keyword arguments to an existing endpoint method:

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

Keys are endpoint method names or {class}`ViewRoute
<fastapi_restly.views.ViewRoute>` values. Use `"put"` for React Admin's PUT
route and the method name for a custom route. Values accept the same keyword
arguments as a route decorator, except its path. An unknown method name raises
{class}`RestlyConfigurationError <fastapi_restly.exc.RestlyConfigurationError>`
at registration. Options for an excluded route are ignored.

The options override decorator metadata, which overrides the defaults. A
subclass inherits the mapping. Declaring a new mapping on the subclass
replaces it, without merging entries from the parent.

Default route names repeat across resources. Set distinct `name` values when
using `app.url_path_for()` to reverse routes from several views. Explicit
`operation_id` values must be unique across the application's OpenAPI schema.

## Per-route metadata on custom routes

To document a custom route, pass keyword arguments to its route decorator.
The decorators forward them to FastAPI's `add_api_route()`, so
custom actions document themselves like any FastAPI endpoint:

```python
    @fr.post(
        "/{id}/publish",
        status_code=200,
        summary="Publish an article",
        responses={409: {"description": "Already published"}},
    )
    async def publish(self, id: int): ...
```

## Change a default CRUD route's documented contract

A default route's `response_model` (and therefore its documented schema) comes
from the view's {attr}`schema <fastapi_restly.views.BaseRestView.schema>` family. OpenAPI shows the classes that
Restly derives from it, such as `UserResponse` and `UserCreate`; see
[Generated Class Names](#generated-class-names). To document (and return) a different
shape on one verb, replace that endpoint method with your own decorator and
`response_model`; see
[Response Envelopes and List Metadata](howto_response_schema.md),
[a different schema for the list endpoint](patterns.md#a-different-schema-for-the-list-endpoint),
and [replacing an endpoint method](customize.md#replace-an-endpoint-method-to-change-the-http-contract).

Routes removed with {attr}`exclude_routes <fastapi_restly.views.BaseRestView.exclude_routes>` disappear from the schema entirely.

## Resource references (`x-resource-ref`)

Reference fields are annotated in the generated spec so clients and
generators can see which resource a scalar id points at. Schema fields
declared with {class}`fr.MustExist[int, Model] <fastapi_restly.schemas.MustExist>`
(a foreign-key column) or {class}`fr.IDRef[Model] <fastapi_restly.schemas.IDRef>` /
{class}`fr.IDSchema[Model] <fastapi_restly.schemas.IDSchema>` (a relationship)
carry the vendor extension `x-resource-ref: "<resource-name>"`. The reference
styles are covered in
[Work with Foreign Keys and Relationships](howto_relationship_idschema.md).

There is a known limit: views included on an `APIRouter` rather than the app
currently lose these annotations. The check for two classes with the same
name still covers them when you pass the app to
{func}`fr.configure(app, ...) <fastapi_restly.db.configure>`; see
[Name your schemas](#name-your-schemas).

## See also

- [Views](class_based_views.md): where the class-level attributes
  come from.
- [Endpoint Decorators](#endpoint-decorators): the decorator
  surface and pass-through keyword arguments.
