# Responses

Every CRUD endpoint method ends with one call,
{meth}`self.to_response(result, shape) <fastapi_restly.views.BaseRestView.to_response>`.
The shape says what `result` is, and `to_response` passes it on:

| Shape | `result` | What `to_response` returns |
|---|---|---|
| {attr}`SINGLE <fastapi_restly.views.ResponseShape.SINGLE>` | one ORM object | {meth}`to_single_response(obj) <fastapi_restly.views.BaseRestView.to_single_response>`: an instance of the response class |
| {attr}`LIST <fastapi_restly.views.ResponseShape.LIST>` | a {class}`ListResult <fastapi_restly.views.ListResult>` | {meth}`to_list_response(result) <fastapi_restly.views.BaseRestView.to_list_response>`: the list envelope, filled with instances of the response class |
| {attr}`EMPTY <fastapi_restly.views.ResponseShape.EMPTY>` | `None` | an empty `204 No Content` response |

FastAPI then serializes the value with the route's `response_model`. Restly
sets it when the view registers: the response class for a single object, and
the list response class for the list. So the response and OpenAPI show the same
shape.

The response class decides which fields go out. By default it is the view's
schema without its `WriteOnly` fields; see
[Set your own response class](#own-response-class). This page covers what is
around the fields, and where each change goes:

- the envelope of every list: the pagination's envelope; see
  [Change the list envelope](#change-list-envelope);
- the shape of one route, such as an envelope around a single object: a
  replaced endpoint method; see [Change the shape of one route](#one-route-shape);
- the shape of every route: `to_response`; see
  [Change the shape of every route](#to-response);
- how one object becomes a response: `to_single_response`; see
  [Change how one object is serialized](#to-single-response);
- a smaller schema for the list route; see
  [Use a different schema for the list route](#list-schema).

Errors do not pass through `to_response`. To change the error shape, see
[Errors](errors.md).

(default-responses)=
## What Restly returns by default

| Route | Response body |
|---|---|
| `GET /{id}`, `POST`, `PATCH` | The bare object, an instance of the response class |
| `GET /` | A `data` envelope around the page, with `total_count`, `page`, `page_size` and `total_pages` |
| `DELETE /{id}` | `204 No Content`, with an empty body |

The list envelope looks like this:

```json
{
  "data": [ /* a page of UserResponse */ ],
  "total_count": 123,
  "page": 2,
  "page_size": 50,
  "total_pages": 3
}
```

A view without pagination sends only `data`; see
[`pagination = None`](#no-pagination). How a client asks for a page, and
the page size limits, are on
[Filtering & Pagination](filtering.md).

The list route's `response_model` is the pagination's envelope, filled with
the response class. By default that envelope is a
{class}`PaginatedEnvelope <fastapi_restly.views.PaginatedEnvelope>`, and a
plain {class}`Envelope <fastapi_restly.views.Envelope>` for a view without
pagination. OpenAPI names the filled class after the response class:
`TagListResponse` for `TagResponse`, which Restly derives from `TagSchema`.

(change-list-envelope)=
## Change the list envelope

The list envelope is a Pydantic model, and you can replace it. Write a
generic model with one type parameter for the items, and set it as the
`envelope` of the view's pagination. Restly fills its fields by name from
`data`, `total_count`, `page`, `page_size` and `total_pages`. The response
and OpenAPI follow the model.

- Leave a field out to drop it from the response.
- Rename a field in the JSON with `alias`, or rename them all with an
  `alias_generator`.
- Give an extra field a default, and it is sent as it is.
- Reshape the values in a `model_validator(mode="before")`, which receives
  them as a dict.

The FastAPI full-stack template sends `{"data": [...], "count": 123}`:

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

Restly builds an empty page from the envelope when you create the pagination
settings. So a required field that Restly cannot fill, such as a misspelled
`totl_count`, fails at startup and not on a request. Leave the envelope's
`extra` setting at its default: Restly passes it every page value, and it
keeps the fields that it declares.

To give every list the same envelope, set the pagination on a project base
view; see [Set the pagination once for every view](#set-pagination-once). A custom list
route can then name the view's list response class, so OpenAPI shows the same
type as for `GET /`; see
[Name the view's classes in a custom route](#name-the-response-classes).
[Coming from fastapi-pagination](#fastapi-pagination-migration) shows the
envelope that keeps fastapi-pagination's field names.

### Change the envelope of a view without pagination

A view that returns every row takes an envelope through
{class}`fr.NoPagination <fastapi_restly.views.NoPagination>`. Restly fills it
from `data` and from `total_count`, the number of rows, and runs no count
query. So the `DataCount` model above works here too:

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
    # Response: [ /* every TagResponse */ ]
```

(one-route-shape)=
## Change the shape of one route

An envelope around a single object changes the HTTP contract. So does a list
shape that no envelope model can express, such as a count in a header. For
these, [replace the endpoint method](#replace-endpoint-method) and set a
matching `response_model` on the replacement.

For a `{"data": ...}` wrapper around a single object, replace
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

Call `to_single_response(obj)` before you put the object in the envelope. It
turns the ORM object into an instance of the response class, which has no
`WriteOnly` fields. The ORM object often has no value for those fields, such
as a plain `password`, so it may not validate as the view's schema.

A replacement for
{meth}`get_many_endpoint <fastapi_restly.views.RestView.get_many_endpoint>`
should keep the `list_params` parameter: Restly fills it with the filter, sort
and page query parameters. To reuse the page numbers, pass the result of
{meth}`handle_get_many <fastapi_restly.views.RestView.handle_get_many>` to
{meth}`to_list_response(result) <fastapi_restly.views.BaseRestView.to_list_response>`.
It returns an instance of the view's envelope model, so read its attributes:
`page.data` and `page.total_count` with the default envelope.

(to-response)=
## Change the shape of every route

When the same wrapper applies to more than one route, put it in
{meth}`to_response() <fastapi_restly.views.BaseRestView.to_response>`. The
default endpoint methods all return through it:

```python
    def to_response(self, result, shape=fr.ResponseShape.SINGLE):
        if shape is fr.ResponseShape.SINGLE:
            return {"data": self.to_single_response(result)}
        return super().to_response(result, shape)
```

This override changes only the value. The `response_model` of each CRUD route
still describes the bare object. So FastAPI's response validation fails, and
OpenAPI shows the wrong shape. A new contract needs both parts: the
`to_response` override for the value, and a replaced endpoint method with a
matching `response_model` for each route that changes; see
[Change the shape of one route](#one-route-shape).

`to_response` knows the shape of the response, not the action. It cannot tell
{meth}`create <fastapi_restly.views.RestView.create>` from
{meth}`get_one <fastapi_restly.views.RestView.get_one>`: both are `SINGLE`.
To change the status code or the headers of one route, replace that endpoint
method; see [Change the status code or headers of one route](#replace-endpoint-method).

(to-single-response)=
## Change how one object is serialized

{meth}`to_single_response(obj) <fastapi_restly.views.BaseRestView.to_single_response>`
turns one ORM object into an instance of the response class. It reads the
attributes of the object and validates them with Pydantic. Each endpoint
method calls it, through `to_response`, and so does each item of a list.

Override it to send other values, or to build the response faster when you
trust the data:

```python
    def to_single_response(self, obj: User):
        return self.schema_response.model_construct(
            id=obj.id,
            name=obj.name,
            email=obj.email,
        )
```

`model_construct()` skips the validators and the check for required fields.
So make sure that the values match the response class. Build an instance of
the response class, not of the view's schema: Restly sends an instance of the
response class as it is, and converts an instance of any other class first.

An override may return an instance of another model, such as the view's
schema. Only the fields of the response class go out; see
[Internals](#response-conversion) for how Restly converts it.

(list-schema)=
## Use a different schema for the list route

There is no `schema_list` attribute. A different list shape changes the HTTP
contract, so it belongs in the endpoint method. Replace
{meth}`get_many_endpoint <fastapi_restly.views.RestView.get_many_endpoint>`
with your own `response_model`, and serialize through the smaller schema.
Filtering, sorting and pagination keep working:

```python
class UserSummary(fr.IDSchema):
    name: str


class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserSchema  # the other routes keep the full schema

    @fr.get("/", response_model=list[UserSummary])
    async def get_many_endpoint(self, list_params):
        result = await self.handle_get_many(list_params)
        return [
            UserSummary.model_validate(u, from_attributes=True)
            for u in result.objects
        ]
```

This route returns a bare list. The filters and sort keys still follow the
view's response class, not `UserSummary`.

## Internals

(response-conversion)=
### How Restly converts the value of `to_single_response`

The response class that Restly builds is a subclass of the view's schema.
Restly passes the value of `to_single_response()` through one more step before
it sends it:

- An instance of the response class goes out as it is.
- An instance of a subclass of the response class, or of the view's schema
  when Restly built the response class, is copied into the response class.
  Restly does not validate the values again: they are already valid, and a
  validator that changes a value would change it twice.
- An instance of another Pydantic model is validated into the response class
  from its attributes. A response class that the view sets itself can have
  other fields and types, so Restly cannot copy into it.

In each case only the fields of the response class go out.

## See also

- [Schemas & Validation](schemas.md): which fields the response
  class has.
- [Request Lifecycle & Hooks](#replace-endpoint-method): how to replace an
  endpoint method.
- [Filtering & Pagination](filtering.md): the page parameters
  that clients send.
- [Errors](errors.md): the error shape, which does not pass
  through `to_response`.
- [Return related objects in a response](#relationship-loading): how nested
  relationship fields load, and `MissingGreenlet` on async.
