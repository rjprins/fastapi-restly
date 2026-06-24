# Customize the OpenAPI Schema

Restly's generated routes are ordinary FastAPI path operations, so everything
OpenAPI-related composes the FastAPI way. This page maps the knobs.

## Per-view metadata

{attr}`tags <fastapi_restly.views.View.tags>`, {attr}`responses <fastapi_restly.views.View.responses>`, and {attr}`dependencies <fastapi_restly.views.View.dependencies>` are class attributes on every view;
they apply to all of the view's routes, generated and custom:

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

The route decorators forward keyword arguments to FastAPI's
`add_api_route()`, so custom actions document themselves like any FastAPI
endpoint:

```python
    @fr.post(
        "/{id}/publish",
        status_code=200,
        summary="Publish an article",
        responses={409: {"description": "Already published"}},
    )
    async def publish(self, id: int): ...
```

## Changing a generated route's documented contract

A generated route's `response_model` (and therefore its documented schema)
comes from the view's {attr}`schema <fastapi_restly.views.BaseRestView.schema>` family. To document — and return — a different
shape on one verb, replace that route shell with your own decorator and
`response_model`; see
[Patterns: a different schema for the list endpoint](patterns.md#a-different-schema-for-the-list-endpoint)
and [Override CRUD Behavior → Tier 1](howto_override_endpoints.md).

Routes removed with {attr}`exclude_routes <fastapi_restly.views.BaseRestView.exclude_routes>` disappear from the schema entirely.

## Resource references (`x-resource-ref`)

Schema fields declared with {class}`fr.IDRef[Model] <fastapi_restly.schemas.IDRef>` are annotated in the generated
spec with a vendor extension, `x-resource-ref: "<resource-name>"`, so clients
and generators can see which resource a scalar id points at. (Known limit:
views included on an `APIRouter` rather than the app currently lose these
annotations.)

## See also

- [Class-Based Views](class_based_views.md) — where the class-level
  attributes come from.
- [API Reference → Endpoint Decorators](api_reference.md) — the decorator
  surface and pass-through kwargs.
