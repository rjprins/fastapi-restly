# Change Response Schemas

Restly uses the view's {attr}`schema <fastapi_restly.views.BaseRestView.schema>`
for generated success responses. That is the right default for normal CRUD, but
public APIs often need one of these changes:

- fewer or different object fields on the wire
- a different schema for one generated route, such as list vs detail
- a top-level envelope such as `{"data": ...}` or `{"data": ..., "meta": ...}`
- the built-in pagination metadata envelope

The rule of thumb: **if the HTTP response contract changes, replace the route
shell and set `response_model`**. Use {meth}`to_response_schema() <fastapi_restly.views.BaseRestView.to_response_schema>`
inside that shell so Restly still applies response-schema validation,
relationship-id handling, aliases, and `WriteOnly` stripping.

## Pick the right override

| Goal | Use |
|---|---|
| Change fields returned by every generated object route | Set {attr}`schema <fastapi_restly.views.BaseRestView.schema>` to an explicit read schema |
| Use a different list schema | Replace {meth}`get_many_endpoint <fastapi_restly.views.RestView.get_many_endpoint>` with `response_model=list[YourListSchema]` |
| Add a `data` envelope around one object route | Replace that route shell with `response_model=YourEnvelope` |
| Add list metadata | Prefer {attr}`include_pagination_metadata <fastapi_restly.views.BaseRestView.include_pagination_metadata>` |
| Add a custom list envelope | Replace {meth}`get_many_endpoint <fastapi_restly.views.RestView.get_many_endpoint>` with a custom envelope response model |
| Change error responses | Use FastAPI exception handlers; errors bypass `to_response()` |

## Change object fields for the whole view

Set the view's `schema` to the public read contract you want. Generated create
and update schemas are derived from it unless you set `schema_create` /
`schema_update` yourself:

```python
class UserRead(fr.IDSchema):
    email: str
    display_name: str


@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserRead
```

For aliases, `ReadOnly`, `WriteOnly`, computed fields, and relationship IDs, see
[Custom Schemas and Field Types](howto_custom_schema.md).

## Return a different schema for the list route

There is no `schema_list` attribute. A different list shape is a route-level
HTTP contract, so replace the list route shell:

```python
class UserSummary(fr.IDSchema):
    display_name: str


@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserRead  # detail, create, and update responses keep UserRead

    @fr.get("/", response_model=list[UserSummary])
    async def get_many_endpoint(self, query_params):
        result = await self.handle_get_many(query_params)
        return [
            UserSummary.model_validate(user, from_attributes=True)
            for user in result.objects
        ]
```

Keep the `query_params` parameter when you want Restly's generated filter, sort,
and pagination query parameters. Restly annotates that parameter when the view is
included.

## Add an envelope around single-object routes

Generated route shells call `to_response()`, but FastAPI still validates the
return value against the generated route's response model. Returning
`{"data": ...}` from `to_response()` alone changes the runtime shape without
changing the documented/validated schema.

Replace the route shell instead:

```python
import pydantic


class UserEnvelope(pydantic.BaseModel):
    data: UserRead


@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserRead

    @fr.get("/{id}", response_model=UserEnvelope)
    async def get_one_endpoint(self, id: int):
        obj = await self.handle_get_one(id)
        return {"data": self.to_response_schema(obj)}

    @fr.post("/", response_model=UserEnvelope)
    async def create_endpoint(self, schema_obj):
        obj = await self.handle_create(schema_obj)
        return {"data": self.to_response_schema(obj)}
```

Use the handler (`handle_get_one`, `handle_create`, `handle_update`, ...) rather
than the bare business method when replacing a generated shell. The handler keeps
authorization, scoped reads, and the commit bracket in place.

## Use the built-in pagination envelope

For the common list metadata shape, set one class attribute:

```python
@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserRead
    include_pagination_metadata = True
```

The list route then returns:

```json
{
  "items": [],
  "total": 123,
  "page": 2,
  "page_size": 50,
  "total_pages": 3
}
```

`page`, `page_size`, and `total_pages` are populated when pagination is active:
the client sent `?page=` / `?page_size=`, or the view set `default_page_size`.

## Add a custom list envelope

For a different envelope, replace the list route shell and keep `query_params`:

```python
import pydantic


class PageMeta(pydantic.BaseModel):
    total: int
    page: int | None = None
    page_size: int | None = None
    total_pages: int | None = None


class UserListEnvelope(pydantic.BaseModel):
    data: list[UserRead]
    meta: PageMeta


@fr.include_view(app)
class UserView(fr.AsyncRestView):
    prefix = "/users"
    model = User
    schema = UserRead
    default_page_size = 50

    @fr.get("/", response_model=UserListEnvelope)
    async def get_many_endpoint(self, query_params):
        result = await self.handle_get_many(query_params)
        page = self.to_paginated_listing_response(query_params, result)
        return {
            "data": page["items"],
            "meta": {
                "total": page["total"],
                "page": page["page"],
                "page_size": page["page_size"],
                "total_pages": page["total_pages"],
            },
        }
```

## When to override `to_response()`

{meth}`to_response() <fastapi_restly.views.BaseRestView.to_response>` is the
shared runtime response boundary used by generated CRUD shells and by custom
actions that call it directly. It is useful when the endpoint's response model
already matches what you return.

Do not use `to_response()` by itself to introduce a new top-level envelope on
generated routes. The generated route annotations still describe the old shape,
so FastAPI response validation and OpenAPI will disagree with the returned
payload. For a new HTTP contract, replace the route shell and set
`response_model`.

## Error envelopes are separate

Errors do not pass through `to_response()` or `to_response_schema()`. Use
FastAPI exception handlers for error response envelopes; see
[Shape Error Responses](howto_error_responses.md).
