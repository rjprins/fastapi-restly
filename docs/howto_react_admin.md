# React Admin Integration

[React-admin](https://marmelab.com/react-admin/) with
[`ra-data-simple-rest`](https://github.com/marmelab/react-admin/tree/master/packages/ra-data-simple-rest)
expects a specific REST wire contract that differs from the
[default Restly contract](howto_response_schema.md#what-restly-returns-by-default)
in several ways: JSON-encoded sort and range parameters, a plain
array response body, and a `Content-Range` header for pagination.
{class}`AsyncReactAdminView <fastapi_restly.views.AsyncReactAdminView>` and {class}`ReactAdminView <fastapi_restly.views.ReactAdminView>` implement this contract, so
`ra-data-simple-rest` works without a custom data provider.

## Quick start

To adopt the contract, replace {class}`AsyncRestView <fastapi_restly.views.AsyncRestView>` with {class}`AsyncReactAdminView <fastapi_restly.views.AsyncReactAdminView>` (or {class}`RestView <fastapi_restly.views.RestView>` with
{class}`ReactAdminView <fastapi_restly.views.ReactAdminView>` for sync sessions):

```python
import fastapi_restly as fr
from fastapi import FastAPI
from sqlalchemy.orm import Mapped

app = FastAPI()
fr.configure(async_database_url="sqlite+aiosqlite:///app.db")

class Product(fr.IDBase):
    name: Mapped[str]
    price: Mapped[float]

@fr.include_view(app)
class ProductView(fr.AsyncReactAdminView):
    prefix = "/products"
    model = Product
```

On the frontend, point `ra-data-simple-rest` at your API:

```jsx
import { Admin, Resource } from "react-admin";
import simpleRestProvider from "ra-data-simple-rest";

const dataProvider = simpleRestProvider("http://localhost:8000");

export default () => (
  <Admin dataProvider={dataProvider}>
    <Resource name="products" />
  </Admin>
);
```

No custom data provider or adapter layer is needed.

## Wire contract

{class}`AsyncReactAdminView <fastapi_restly.views.AsyncReactAdminView>` translates the `ra-data-simple-rest` query format to SQL
and returns responses the provider expects.

### List: `GET /resource/`

The list endpoint reads react-admin's three JSON-encoded query parameters:

| Query parameter | Format | Example |
|---|---|---|
| `sort` | JSON `[field, direction]` | `sort=["name","ASC"]` |
| `range` | JSON `[start, end]` (inclusive) | `range=[0,24]` |
| `filter` | JSON object | `filter={"name":"foo"}` or `filter={"id":[1,2,3]}` |

The response body is a plain JSON array, and the `Content-Range` header
carries the total:

```
Content-Range: items 0-24/315
```

The `id` array form of `filter` (`{"id": [1, 2, 3]}`) is used by react-admin
for `getMany` calls. It translates to `WHERE id IN (1, 2, 3)`.

### Other operations

Four of the remaining routes serve react-admin's other data provider calls;
the standard PATCH route stays available alongside them:

| Method | Path | Purpose | Source |
|---|---|---|---|
| `GET` | `/{id}` | Get one (react-admin `getOne`) | inherited from {class}`AsyncRestView <fastapi_restly.views.AsyncRestView>` |
| `POST` | `/` | Create (react-admin `create`) | inherited from `AsyncRestView` |
| `PUT` | `/{id}` | Full update (react-admin `update`) | added by {class}`AsyncReactAdminView <fastapi_restly.views.AsyncReactAdminView>` |
| `PATCH` | `/{id}` | Partial update | inherited from `AsyncRestView` |
| `DELETE` | `/{id}` | Delete (react-admin `delete`) | inherited from `AsyncRestView` |

`AsyncReactAdminView` and {class}`ReactAdminView <fastapi_restly.views.ReactAdminView>` add a `PUT /{id}` endpoint because
`ra-data-simple-rest`'s default `update` method issues a `PUT` request. The
default `PATCH /{id}` is also kept available, so clients that prefer partial
updates continue to work.

The PUT route delegates to the same {meth}`handle_update <fastapi_restly.views.RestView.handle_update>` request handler as PATCH and
accepts the view's standard {attr}`schema_update <fastapi_restly.views.BaseRestView.schema_update>` payload. If you need different
write semantics for the two methods, override the {meth}`update <fastapi_restly.views.RestView.update>` business verb (or
`handle_update`, or replace the PUT route directly);
[How Overrides Work: The Three Tiers](the_handle_design.md) explains how
these override points relate.

## Serialize related lists as scalar id arrays with `IDRef`

`ra-data-simple-rest` expects `to-many` references as plain id arrays
(`"products": [1, 2, 3]`). Declare the field as `list[fr.IDRef[Product]]`;
[Lists of References](howto_relationship_idschema.md#lists-of-references)
describes the full behavior. The [shop example](examples.md#shop) runs this
end to end, React Admin frontend included.

## CORS setup

Browsers block non-standard response headers by default. The `Content-Range`
header must be explicitly exposed in your CORS configuration, otherwise the
frontend cannot read the total and pagination breaks.

Add `CORSMiddleware` to your FastAPI app with `Content-Range` in
`expose_headers`:

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # your frontend origin
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Range"],
)
```

{class}`AsyncReactAdminView <fastapi_restly.views.AsyncReactAdminView>` also sets `Access-Control-Expose-Headers: Content-Range`
on list responses as a fallback. Prefer middleware in production.

## Customization

A few view-level settings adjust the defaults described above, and a base
class can share them across views.

### Set the default page size

When the frontend does not send a `range` query parameter, Restly returns the
first 25 rows. Set {attr}`default_page_size <fastapi_restly.views.BaseRestView.default_page_size>` on the view to choose a different
default:

```python
@fr.include_view(app)
class ProductView(fr.AsyncReactAdminView):
    prefix = "/products"
    model = Product
    default_page_size = 50
```

### Change the Content-Range unit

`ra-data-simple-rest` ignores the unit part of the header (it only parses the
numbers), but if another consumer cares, override `get_react_admin_range_unit`:

```python
class ProductView(fr.AsyncReactAdminView):
    prefix = "/products"
    model = Product

    def get_react_admin_range_unit(self) -> str:
        return "products"
```

### Share the react-admin contract across multiple views

Put shared customizations in a project base class and inherit your views from
it, following the pattern in
[Share Behaviour with Base Views](howto_inheritance.md):

```python
class ReactAdminBase(fr.AsyncReactAdminView):
    default_page_size = 100

    def get_react_admin_range_unit(self) -> str:
        return "items"

@fr.include_view(app)
class ProductView(ReactAdminBase):
    prefix = "/products"
    model = Product

@fr.include_view(app)
class CustomerView(ReactAdminBase):
    prefix = "/customers"
    model = Customer
```

## Under the hood

{class}`AsyncReactAdminView <fastapi_restly.views.AsyncReactAdminView>` is a thin subclass of {class}`AsyncRestView <fastapi_restly.views.AsyncRestView>` built with the
[route replacement](howto_override_endpoints.md#tier-1-replace-a-route-shell-to-change-the-http-contract)
pattern. It replaces the {meth}`get_many_endpoint <fastapi_restly.views.RestView.get_many_endpoint>` route shell to parse the
react-admin query string, then delegates to the standard {meth}`handle_get_many <fastapi_restly.views.RestView.handle_get_many>` /
{meth}`get_many <fastapi_restly.views.RestView.get_many>` flow. The react-admin dialect itself lives in {meth}`apply_query_params <fastapi_restly.views.RestView.apply_query_params>`
(JSON `sort` / `range` / `filter`) and `to_response(..., ResponseShape.LISTING)`
(plain array body plus `Content-Range`).

It also adds a `PUT /{id}` route that delegates to the standard {meth}`handle_update <fastapi_restly.views.RestView.handle_update>`
request handler. All other generated routes (`GET /{id}`, `POST /`,
`PATCH /{id}`, `DELETE /{id}`) and write business-verb tiers are inherited
unchanged.

The shared parsing and response helpers are internal.
