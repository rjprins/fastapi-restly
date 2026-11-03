# Responses

A CRUD endpoint method returns the row, or the list result. Each route declares
the view's response, so Restly knows what to do with this value:

| The endpoint method returns | Restly calls | Which gives |
|---|---|---|
| one ORM object | {meth}`to_single_response(obj) <fastapi_restly.views.BaseRestView.to_single_response>` | an instance of the response class |
| a {class}`ListResult <fastapi_restly.views.ListResult>` | {meth}`to_list_response(result) <fastapi_restly.views.BaseRestView.to_list_response>` | the list envelope, filled with instances of the response class |

`DELETE` returns an empty `204 No Content` response.

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
- the response of a custom route that returns a row; see
  [The response of a custom route](#custom-route-response);
- the shape of one route, such as an envelope around a single object: a
  replaced endpoint method; see [Change the shape of one route](#one-route-shape);
- the shape of every route: endpoint methods replaced on a base view; see
  [Change the shape of every route](#every-route-shape);
- how one object becomes a response: `to_single_response`; see
  [Change how one object is serialized](#to-single-response);
- a smaller schema for the list route; see
  [Use a different schema for the list route](#list-schema).

Errors do not pass through these methods. To change the error shape, see
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
[Filtering, Sorting & Pagination](filtering.md).

The list route's `response_model` is the view's
{attr}`list_response_schema <fastapi_restly.views.BaseRestView.list_response_schema>`.
Unless the view sets another class, this is the pagination's envelope, filled
with the response class. By default that envelope is a
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

To give every list the same envelope, set the pagination on a project base
view; see [Set the pagination once for every view](#set-pagination-once). A custom list
route can then name the view's list response class, so OpenAPI shows the same
type as for `GET /`; see
[Name the view's classes in a custom route](#name-the-response-classes).
[Coming from fastapi-pagination](#fastapi-pagination-migration) shows the
envelope that keeps fastapi-pagination's field names.

:::{admonition} Details
:class: details

An envelope with camelCase field names, `totalCount`, `pageSize` and
`totalPages`:

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
:::

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

(custom-route-response)=
## The response of a custom route

A custom route that declares the view's response class goes through the same
response step as the CRUD routes. So it can return the row itself:

```python
from fastapi_restly.views import IDType, ResponseSchema


@fr.include_view(app)
class ProjectView(fr.AsyncRestView):
    prefix = "/projects"
    model = Project
    schema = ProjectSchema

    class Action(fr.Action):
        ARCHIVE = "archive"

    @fr.post("/{id}/archive")
    async def archive(self, id: IDType) -> ResponseSchema:
        project = await self.get_one(id)
        async with self.write_action(self.Action.ARCHIVE, obj=project):
            project.archived = True
        return project
```

Restly passes the row to
{meth}`to_single_response <fastapi_restly.views.BaseRestView.to_single_response>`,
as `PATCH /{id}` does. So an override of it changes this response too, and OpenAPI shows `ProjectResponse`.

What Restly does depends on what the route declares, in its return
annotation or in `response_model=`:

| The route declares | The route returns | Restly sends |
|---|---|---|
| {data}`ResponseSchema <fastapi_restly.views.ResponseSchema>` | a row of the view's model | `to_single_response(row)` |
| `list[ResponseSchema]` | a list of rows, or another iterable of rows, such as `await self.session.scalars(stmt)` | `to_single_response(row)` for each row |
| {data}`ListResponseSchema <fastapi_restly.views.ListResponseSchema>` | a {class}`ListResult <fastapi_restly.views.ListResult>` | `to_list_response(result)` |
| the view's response class itself, as in `response_model=ProjectResponse` | a row | the same as for `ResponseSchema` |
| the view's model, as in `-> Project` | a row | the same as for `ResponseSchema` |
| `list[Project]` | a list of rows | the same as for `list[ResponseSchema]` |
| nothing, `response_model=None`, or a type without fields, such as `Any`, `dict` or `dict[str, Any]` | a row of the view's model, a list of rows, a `ListResult`, or a dict with one of these as a value | the same as for `ResponseSchema` and `ListResponseSchema`, and any other value goes out as it is |

Each of these may allow `None`, as in `-> ResponseSchema | None`, and a `None`
goes out as `null`. Any other value goes out as it is: a dict, an instance
of a schema, or a `fastapi.Response`. So a route that calls
`self.to_single_response(row)` itself works the same way.

A route that declares nothing shows an empty schema in OpenAPI. So declare the
response when the route has one.

On such a route, Restly looks at the value itself, at the first item of a list,
and at the first 20 values of a dict. A row anywhere else goes out with each
column, as in FastAPI: in `{"a": {"b": row}}`, in a tuple, in the result of
`session.execute(stmt).all()`, or in a field of type `Any` of your own class. A
row of another model also goes out with each column. So declare the response if
a column holds a secret.

:::{admonition} Details
:class: details

Restly reads the fields of the response class from the row, so the row must
have what they need. In async code, a relationship that your own `select` did
not load raises `MissingGreenlet`. A `ListResult` inside a dict gives only its
items if `to_list_response` returns a `Response`, as on a react-admin view.

A route that names another class, such as
`response_model=ProjectSummary`, keeps FastAPI's behaviour. FastAPI builds
the response from the value that the route returns, and
`to_single_response` does not run. So a value that `to_single_response`
adds is missing, and a change that it makes does not happen. When it cleans
up a stored value, such as an old color code, the stored value can fail the
validation of the response, and the client gets a `500`. To run
`to_single_response`, declare the view's response.

The view's model counts only in the return annotation, not in
`response_model=`. Restly then sets the view's response class as the
`response_model`. This is not what plain FastAPI does: FastAPI cannot use a
SQLAlchemy class as a response model. But the view sets the response class
for its model, so a route that returns the model returns the view's
response.

The markers also give a custom route the view's types in OpenAPI; see
[Name the view's classes in a custom route](#name-the-response-classes).
:::

(one-route-shape)=
## Change the shape of one route

An envelope around a single object changes the HTTP contract. So does a list
shape that no envelope model can express, such as a count in a header. For
these, [replace the endpoint method](#replace-endpoint-method) and declare the
new shape on the replacement.

For a `{"data": ...}` wrapper around a single object, write a generic class, and
replace
{meth}`get_one_endpoint <fastapi_restly.views.RestView.get_one_endpoint>` and
{meth}`create_endpoint <fastapi_restly.views.RestView.create_endpoint>`:

```python
from typing import Generic, TypeVar

import pydantic

from fastapi_restly.views import CreateSchema, IDType, ResponseSchema

T = TypeVar("T")


class Data(pydantic.BaseModel, Generic[T]):
    data: T


@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserSchema

    @fr.get("/{id}")
    async def get_one_endpoint(self, id: IDType) -> Data[ResponseSchema]:
        obj = await self.handle_get_one(id)
        return {"data": self.to_single_response(obj)}

    @fr.post("/")
    async def create_endpoint(self, data: CreateSchema) -> Data[ResponseSchema]:
        obj = await self.handle_create(data)
        return {"data": self.to_single_response(obj)}
```

Call `to_single_response(obj)` before you put the object in the envelope.
The markers keep the view's classes; see
[Name the view's classes in a custom route](#name-the-response-classes).

:::{admonition} Details
:class: details

It turns the ORM object into an instance of the response class, which has no
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
:::

(every-route-shape)=
## Change the shape of every route

When every view has the same contract, replace the endpoint methods once, on a
base view. A marker stands for the class of each view that registers, so each
view gets its own type in OpenAPI:

```python
class ApiView(fr.AsyncRestView):
    @fr.get("/{id}")
    async def get_one_endpoint(self, id: IDType) -> Data[ResponseSchema]:
        obj = await self.handle_get_one(id)
        return {"data": self.to_single_response(obj)}

    @fr.post("/")
    async def create_endpoint(self, data: CreateSchema) -> Data[ResponseSchema]:
        obj = await self.handle_create(data)
        return {"data": self.to_single_response(obj)}
```

`Data` is the class from [Change the shape of one route](#one-route-shape). A
view that subclasses `ApiView` gets `Data[UserResponse]` in OpenAPI, and another
view gets `Data[TagResponse]`. For a shape that differs per view, such as a root
key, as in `{"article": {...}}`, write a function that builds the class; see
[Build a class for each view](#schema-type). To change the status code or the
headers of one route, replace that endpoint method; see
[Change the status code or headers of one route](#replace-endpoint-method).

(to-single-response)=
## Change how one object is serialized

{meth}`to_single_response(obj) <fastapi_restly.views.BaseRestView.to_single_response>`
turns one ORM object into an instance of the response class. It reads the
attributes of the object and validates them with Pydantic. It runs for each
row that a route returns, and for each item of a list.

Override it to send other values, or to build the response faster when you
trust the data:

```python
    def to_single_response(self, obj: User):
        return self.response_schema.model_construct(
            id=obj.id,
            name=obj.name,
            email=obj.email,
        )
```

`model_construct()` skips the validators and the check for required fields.
So make sure that the values match the response class. Build an instance of
the response class, not of the view's schema: Restly sends an instance of the
response class as it is, and converts an instance of any other class first.

:::{admonition} Details
:class: details

An override may return an instance of another model, such as the view's
schema. Only the fields of the response class go out; see
[Internals](#response-conversion) for how Restly converts it.
:::

(list-schema)=
## Use a different schema for the list route

Set {attr}`list_response_schema <fastapi_restly.views.BaseRestView.list_response_schema>`
on the view. OpenAPI follows it. The other routes keep the full schema, and
filtering, sorting and pagination keep working:

```python
from fastapi_restly.views import PaginatedEnvelope


class UserSummary(fr.IDSchema):
    name: str


class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserSchema
    list_response_schema = PaginatedEnvelope[UserSummary]
```

This class changes what the list sends, and nothing else. The filters and sort
keys follow the response class, not `UserSummary`. So a client can still filter
and sort on a field that the items do not show. To hide a field, make it
`WriteOnly`, or take it out of the list with `exclude_filters` and
`exclude_sort_keys`.

:::{admonition} Details
:class: details

Restly fills this class as it fills an
[envelope](#change-list-envelope): by field name, from `data`, `total_count`,
`page`, `page_size` and `total_pages`. An alias renames a key in the JSON. Any
other field keeps its default. Registration raises for a class that `GET /`
cannot fill: a class with page fields on a view without pagination, or a class
without a field named `data`.

Restly fills the items from the response class, so an item can have fewer
fields than the response class. An item must be a Pydantic class, and
registration raises for a field without a default that the response class does
not have. The view still loads what the response class needs, so a smaller class
makes the response smaller, not the query.

A list type, such as `list[UserSummary]`, sends a JSON array without the page
values.

The setting also takes a function, so a base view can give each view other keys,
as in `{"articles": [...], "articlesCount": 2}`; see
[Build a class for each view](#schema-type).

A subclass of the view keeps the class. A subclass that sets a new `schema` or
`response_schema` gets the default again, as for the
[other classes](#subclass-new-classes).

For a shape that Restly cannot fill, set the type and replace
{meth}`get_many_endpoint <fastapi_restly.views.RestView.get_many_endpoint>`. The
marker {data}`ListResponseSchema <fastapi_restly.views.ListResponseSchema>` then
gives the route this type. This route sends a map from id to name:

```python
from fastapi_restly.views import ListResponseSchema


class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserSchema
    list_response_schema = dict[int, str]

    @fr.get("/")
    async def get_many_endpoint(self, list_params) -> ListResponseSchema:
        result = await self.handle_get_many(list_params)
        return {user.id: user.name for user in result.objects}
```
:::

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
- [Filtering, Sorting & Pagination](filtering.md): the page parameters
  that clients send.
- [Errors](errors.md): the error shape, which does not pass through the
  response methods.
- [Return related objects in a response](#relationship-loading): how nested
  relationship fields load, and `MissingGreenlet` on async.
