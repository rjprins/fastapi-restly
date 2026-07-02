# Response Envelopes and List Metadata

Restly returns bare objects and bare arrays; this page changes the *container*
around the data, not the fields inside it.

- To change which fields an object exposes, see [Custom Schemas and Field Types](howto_custom_schema.md).
- For a different schema per route (list vs detail), see [Override CRUD Behavior and Add Custom Endpoints](howto_override_endpoints.md).
- To change the error shape, see [Shape Error Responses](howto_error_responses.md).

## What Restly returns by default

| Route | Response body |
|---|---|
| `GET /{id}`, `POST`, `PATCH` | The bare object, serialized through {attr}`schema <fastapi_restly.views.BaseRestView.schema>` via {meth}`to_response_schema <fastapi_restly.views.BaseRestView.to_response_schema>` |
| `GET /` | A bare JSON array — no envelope, no total |
| `DELETE /{id}` | `204 No Content`, empty body |

## List metadata and total count

For the common list-metadata shape — items plus a total — set one class
attribute:

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
  "items": [ /* page of UserRead */ ],
  "total": 123,
  "page": 2,
  "page_size": 50,
  "total_pages": 3
}
```

`total` is always present. `page`, `page_size`, and `total_pages` populate only
when a page size is in effect — the client sent `?page_size=`, or the view set
{attr}`default_page_size <fastapi_restly.views.BaseRestView.default_page_size>`
(in which case `page` defaults to 1); otherwise they are `null`.

This is the **only** envelope where Restly keeps `response_model` and OpenAPI in
sync automatically: when {attr}`include_pagination_metadata <fastapi_restly.views.BaseRestView.include_pagination_metadata>`
is set, the view swaps a generated pagination schema in as the list route's
response annotation — zero route-shell code.

For how clients *request* pages (the `page` / `page_size` inputs), see
[Filter, Sort, and Paginate Lists](howto_query_modifiers.md).

## Custom envelopes

An envelope is an HTTP-contract change, so **replace the route shell and set
`response_model`**. Call {meth}`to_response_schema(obj) <fastapi_restly.views.BaseRestView.to_response_schema>`
inside the shell so `WriteOnly` stripping, relationship-id resolution, and
response-schema validation still run.

For a single-object `{"data": ...}` wrapper, replace
{meth}`get_one_endpoint <fastapi_restly.views.RestView.get_one_endpoint>` and
{meth}`create_endpoint <fastapi_restly.views.RestView.create_endpoint>`:

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

For a list `{"data": ..., "meta": ...}` wrapper, replace
{meth}`get_many_endpoint <fastapi_restly.views.RestView.get_many_endpoint>`,
keep the `query_params` parameter (Restly annotates it with the generated
filter, sort, and pagination query parameters), and reshape
{meth}`to_paginated_listing_response() <fastapi_restly.views.BaseRestView.to_paginated_listing_response>`
so the page math is not redone by hand:

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

### Envelope several routes at once: `to_response`

When the same wrapper applies to more than one route, centralize it in
{meth}`to_response() <fastapi_restly.views.BaseRestView.to_response>` — the shared
runtime boundary keyed on wire {attr}`SINGLE <fastapi_restly.views.ResponseShape.SINGLE>` /
{attr}`LISTING <fastapi_restly.views.ResponseShape.LISTING>` /
{attr}`EMPTY <fastapi_restly.views.ResponseShape.EMPTY>` shape — and have each
replaced shell delegate to it:

```python
    def to_response(self, obj_or_list, shape=fr.ResponseShape.SINGLE):
        if shape is fr.ResponseShape.SINGLE:
            return {"data": self.to_response_schema(obj_or_list)}
        return super().to_response(obj_or_list, shape)
```

The trap: overriding `to_response` **without** also replacing the shells leaves
the generated shells' `response_model` describing the bare object, so FastAPI
response validation *and* OpenAPI disagree with the enveloped payload you return
— so a new contract needs **both**: the `to_response` override for the runtime
shape and a replaced shell with a matching `response_model`.

## See also

- [Custom Schemas and Field Types](howto_custom_schema.md) — which fields an object exposes.
- [Override CRUD Behavior and Add Custom Endpoints](howto_override_endpoints.md) — route-shell mechanics and [a different schema for the list endpoint](patterns.md#a-different-schema-for-the-list-endpoint).
- [Shape Error Responses](howto_error_responses.md) — errors bypass `to_response`.
- [Filter, Sort, and Paginate Lists](howto_query_modifiers.md) — the pagination inputs clients send.
