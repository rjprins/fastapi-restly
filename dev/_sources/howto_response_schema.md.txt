# Response Envelopes and List Metadata

Restly returns bare objects and a `data` envelope for lists; this page covers
changing the *container* around the data, not the fields inside it.

- To change which fields an object exposes, see [Custom Schemas and Field Types](howto_custom_schema.md).
- For a different schema per route (list vs detail), see [Customizing RestView](customize.md).
- To change the error shape, see [Shape Error Responses](howto_error_responses.md).

## What Restly returns by default

Before changing the container, it helps to know what the default routes put
on the wire:

| Route | Response body |
|---|---|
| `GET /{id}`, `POST`, `PATCH` | The bare object, serialized through {attr}`schema <fastapi_restly.views.BaseRestView.schema>` via {meth}`to_single_response <fastapi_restly.views.BaseRestView.to_single_response>` |
| `GET /` | A `data` envelope wrapping the page, plus pagination metadata (`total_count` / `page` / `page_size` / `total_pages`) |
| `DELETE /{id}` | `204 No Content`, empty body |

## The list envelope

List endpoints paginate by default: the route wraps the page of objects in a
`data` envelope and adds pagination metadata.

```json
{
  "data": [ /* page of UserSchema */ ],
  "total_count": 123,
  "page": 2,
  "page_size": 50,
  "total_pages": 3
}
```

When the client omits `?page_size=`, the endpoint uses
{attr}`default_page_size <fastapi_restly.views.NumberedPagination.default_page_size>`
(50). An explicit `page_size` may be smaller or larger than that default;
{attr}`max_page_size <fastapi_restly.views.NumberedPagination.max_page_size>` is
the ceiling above which the request is rejected with 422. Both are settings of
the view's {attr}`pagination <fastapi_restly.views.BaseRestView.pagination>`.

To return every matching row uncapped, set `pagination` to `None`, short for
{class}`fr.NoPagination() <fastapi_restly.views.NoPagination>`. The `data`
envelope stays, but the count and page fields drop away:

```python
@fr.include_view(app)
class TagView(fr.AsyncRestView):
    prefix = "/tags"
    model = Tag
    schema = TagSchema
    pagination = None
    # Response: {"data": [ /* every TagSchema */ ]}
```

Restly keeps `response_model` and OpenAPI in sync with the envelope
automatically: the list route's response annotation is the pagination's
envelope filled with the response class, by default a
{class}`PaginatedEnvelope <fastapi_restly.views.PaginatedEnvelope>` (a plain
{class}`Envelope <fastapi_restly.views.Envelope>` without pagination), so no
endpoint method code is needed. OpenAPI names it after the view's schema:
`TagListResponse` for `TagSchema`.

For how clients *request* pages (the `page` and `page_size` inputs), see
[Pagination](howto_query_modifiers.md#pagination) in the query-modifiers
guide.

(change-list-envelope)=
## Change the list envelope

The list envelope is a Pydantic model, and you can replace it. Write a
generic model with one type parameter for the items, and set it as the
`envelope` of the view's pagination. Restly fills its fields by name from
`data`, `total_count`, `page`, `page_size` and `total_pages`. The response
and OpenAPI follow the model.

- Leave a field out to drop it from the response.
- Rename a field on the wire with `alias`, or rename them all
  with an `alias_generator`.
- Give an extra field a default, and it is sent as is.
- Reshape the values in a `model_validator(mode="before")`, which receives
  them as a dict.

The FastAPI full-stack template's `{"data": [...], "count": 123}`:

```python
from typing import Generic, TypeVar

import pydantic

T = TypeVar("T")


class DataCount(pydantic.BaseModel, Generic[T]):
    data: list[T]
    total_count: int = pydantic.Field(alias="count")


@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserSchema
    pagination = fr.NumberedPagination(envelope=DataCount)
```

camelCase field names, `totalCount`, `pageSize` and `totalPages`:

```python
from pydantic.alias_generators import to_camel


class CamelPage(pydantic.BaseModel, Generic[T]):
    model_config = pydantic.ConfigDict(alias_generator=to_camel)

    data: list[T]
    total_count: int
    page: int
    page_size: int
    total_pages: int
```

The metadata nested under `meta`:

```python
class PageMeta(pydantic.BaseModel):
    total_count: int
    page: int
    page_size: int
    total_pages: int


class DataMeta(pydantic.BaseModel, Generic[T]):
    data: list[T]
    meta: PageMeta

    @pydantic.model_validator(mode="before")
    @classmethod
    def nest_meta(cls, values):
        if isinstance(values, dict) and "meta" not in values:
            values = dict(values)
            return {"data": values.pop("data"), "meta": values}
        return values
```

Set the pagination on a project base view to give every list the same
envelope; see [Set it once for every view](#set-pagination-once).
A custom list route on such a view names the same envelope class as its
response model, filled with the view's schema, for example
`response_model=APP_PAGINATION.envelope[ItemSchema]`. OpenAPI shows it as a
type of its own, next to the `ItemListResponse` of `GET /`; see
[Generated Class Names](#generated-class-names).
[Coming from fastapi-pagination](#fastapi-pagination-migration)
shows the envelope that keeps fastapi-pagination's field names.

Creating the pagination settings builds an empty page from the envelope, so
a required field Restly cannot fill, such as a misspelled `totl_count`, fails
at startup instead of on a request. Leave the envelope's `extra` setting at
its default: Restly passes it every page value, and it keeps the fields it
declares.

### Without pagination

A view that returns every row takes an envelope through
{class}`fr.NoPagination <fastapi_restly.views.NoPagination>`. Restly fills it
from `data` and `total_count`, the number of rows, and runs no count query. So
the `{"data": [...], "count": 123}` model above works here too:

```python
@fr.include_view(app)
class TagView(fr.AsyncRestView):
    prefix = "/tags"
    model = Tag
    schema = TagSchema
    pagination = fr.NoPagination(envelope=DataCount)
```

For a bare JSON array, use a generic `RootModel` that keeps only `data`:

```python
class Items(pydantic.RootModel[list[T]], Generic[T]):
    @pydantic.model_validator(mode="before")
    @classmethod
    def rows_only(cls, values):
        return values["data"] if isinstance(values, dict) else values


@fr.include_view(app)
class TagView(fr.AsyncRestView):
    prefix = "/tags"
    model = Tag
    schema = TagSchema
    pagination = fr.NoPagination(envelope=Items)
    # Response: [ /* every TagSchema */ ]
```

## Custom envelopes

An envelope around a single object, or a list shape no envelope model can
express, such as a header, is a change to the HTTP contract. So
[replace the endpoint method](customize.md#replace-an-endpoint-method-to-change-the-http-contract)
and set `response_model` on the replacement.

In the replacement endpoint method, call
{meth}`to_single_response(obj) <fastapi_restly.views.BaseRestView.to_single_response>`
before placing the object in the envelope. This converts the ORM object to
an instance of the view's schema, without requiring write-only input fields.

For a single-object `{"data": ...}` wrapper, replace
{meth}`get_one_endpoint <fastapi_restly.views.RestView.get_one_endpoint>` and
{meth}`create_endpoint <fastapi_restly.views.RestView.create_endpoint>`:

```python
import pydantic


class UserEnvelope(pydantic.BaseModel):
    data: UserSchema


@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserSchema

    @fr.get("/{id}", response_model=UserEnvelope)
    async def get_one_endpoint(self, id: int):
        obj = await self.handle_get_one(id)
        return {"data": self.to_single_response(obj)}

    @fr.post("/", response_model=UserEnvelope)
    async def create_endpoint(self, schema_obj):
        obj = await self.handle_create(schema_obj)
        return {"data": self.to_single_response(obj)}
```

A replacement for
{meth}`get_many_endpoint <fastapi_restly.views.RestView.get_many_endpoint>` keeps
the `list_params` parameter, which Restly annotates with the generated filter,
sort, and pagination query parameters. To reuse the page math, pass the
result of `handle_get_many` to
{meth}`to_list_response(result) <fastapi_restly.views.BaseRestView.to_list_response>`.
It returns an instance of the view's envelope model, so read its attributes, such
as `page.data` and `page.total_count` with the default envelope.

### Envelope several routes at once: `to_response`

When the same wrapper applies to more than one route, centralize it in
{meth}`to_response() <fastapi_restly.views.BaseRestView.to_response>` and have
each replacement endpoint method delegate to it. `to_response` is the shared runtime
boundary keyed on the wire shape:
{attr}`SINGLE <fastapi_restly.views.ResponseShape.SINGLE>`,
{attr}`LIST <fastapi_restly.views.ResponseShape.LIST>`, or
{attr}`EMPTY <fastapi_restly.views.ResponseShape.EMPTY>`. Its place among the
override points is covered in
[Customizing RestView](customize.md#to_response-the-one-response-method).

```python
    def to_response(self, result, shape=fr.ResponseShape.SINGLE):
        if shape is fr.ResponseShape.SINGLE:
            return {"data": self.to_single_response(result)}
        return super().to_response(result, shape)
```

Be aware that overriding `to_response` without also replacing the endpoint
methods leaves their `response_model` describing the bare object, so
FastAPI response validation *and* OpenAPI disagree with the enveloped payload
you return. A new contract therefore needs both pieces: the `to_response`
override for the runtime shape, and a replacement endpoint method with a matching
`response_model`.

## See also

- [Custom Schemas and Field Types](howto_custom_schema.md): which fields an object exposes.
- [Customizing RestView](customize.md): endpoint-method replacement mechanics, and [a different schema for the list endpoint](patterns.md#a-different-schema-for-the-list-endpoint).
- [Shape Error Responses](howto_error_responses.md): errors bypass `to_response`.
- [Filter, Sort, and Paginate Lists](howto_query_modifiers.md): the pagination inputs clients send.
- [Relationship Loading and Async](howto_relationship_loading.md): how nested relationship fields load, and `MissingGreenlet` on async.
