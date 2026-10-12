# Technical Details

This page documents the implementation behaviour beneath FastAPI-Restly's
public API: how schemas are generated and derived, how view classes register
their routes, how list parameters are assembled, and which session defaults
the framework sets.

(list-parameters-lifecycle)=
## List Parameters Lifecycle

List endpoints accept URL query parameters of the form
``name=John``, ``age__gte=18``, ``sort=-created_at``, and
``page=2&page_size=50``. The full operator surface (`__ne`, `__isnull`,
`__contains`, `__icontains`, and more) is documented in
[Filtering & Pagination](howto_query_modifiers.md).

During {meth}`before_include_view() <fastapi_restly.views.BaseRestView.before_include_view>`, the framework freezes a single class-level
attribute, {attr}`cls.schema_list_params <fastapi_restly.views.BaseRestView.schema_list_params>`: the list params, a Pydantic model generated
by {func}`derive_schema_list_params(cls.schema_response, cls.model, pagination=cls.pagination) <fastapi_restly.query.derive_schema_list_params>`. It covers pagination, sorting, and one filter
parameter per field of the response class that maps to a filterable column on the
model, with optional operator suffixes. A client can filter and sort only on
what it can see. It is generated once per registration
and never re-derived.

A route reads the list params through a dependency, not as a FastAPI query model.
FastAPI splits a query model into separate parameters only when it is the
route's only query parameter, so a dependency's `?api_key=` would collapse
the filters into one required object. Restly validates the model from the
query string itself and documents each key as its own OpenAPI parameter,
next to whatever else the route reads.

Custom dialects (e.g. react-admin's
[`AsyncReactAdminView` / `ReactAdminView`](howto_react_admin.md)) live as
parallel view classes that bypass {func}`apply_list_params <fastapi_restly.query.apply_list_params>` entirely and
implement their own request/response contract.

## See Also

- [Filtering & Pagination](howto_query_modifiers.md): the full
  filter, sort, and pagination reference.
