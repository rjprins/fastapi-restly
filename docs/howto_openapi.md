# Customize the OpenAPI Schema

Restly's generated routes are ordinary FastAPI path operations, so everything
OpenAPI-related composes the FastAPI way. This page maps the customization
points.

## Per-view metadata

To apply OpenAPI metadata across a whole view, set the
{attr}`tags <fastapi_restly.views.View.tags>`, {attr}`responses <fastapi_restly.views.View.responses>`, and {attr}`dependencies <fastapi_restly.views.View.dependencies>` class attributes;
they exist on every view and apply to all of its routes, generated and custom:

```python
@fr.include_view(app)
class InvoiceView(fr.AsyncRestView):
    prefix = "/invoices"
    tags = ["billing"]
    responses = {402: {"description": "Payment required"}}
    model = Invoice
    schema = InvoiceRead
```

## Per-route metadata on custom routes

To document a custom route, pass keyword arguments to its route decorator;
the decorators forward them to FastAPI's `add_api_route()`, so
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

## Change a generated route's documented contract

A generated route's `response_model` (and therefore its documented schema)
comes from the view's {attr}`schema <fastapi_restly.views.BaseRestView.schema>` family. To document (and return) a different
shape on one verb, replace that route shell with your own decorator and
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
currently lose these annotations.

## See also

- [Class-Based Views](class_based_views.md): where the class-level attributes
  come from.
- [Endpoint Decorators](api_reference.md#endpoint-decorators): the decorator
  surface and pass-through keyword arguments.
