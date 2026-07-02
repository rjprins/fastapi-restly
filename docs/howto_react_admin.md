# React Admin Integration

[React-admin](https://marmelab.com/react-admin/) with
[`ra-data-simple-rest`](https://github.com/marmelab/react-admin/tree/master/packages/ra-data-simple-rest)
expects a REST dialect that differs from the
[default Restly contract](howto_response_schema.md#what-restly-returns-by-default)
in three ways: JSON-encoded `sort`, `range`, and `filter` query parameters, a
`Content-Range` response header carrying the total row count, and `PUT`-based
updates. The response body already matches, since both dialects return a plain
JSON array from list endpoints.
{class}`AsyncReactAdminView <fastapi_restly.views.AsyncReactAdminView>` and {class}`ReactAdminView <fastapi_restly.views.ReactAdminView>` implement this dialect, so
`ra-data-simple-rest` works without a custom data provider.

## Quick start

To adopt the contract, replace {class}`AsyncRestView <fastapi_restly.views.AsyncRestView>` with {class}`AsyncReactAdminView <fastapi_restly.views.AsyncReactAdminView>` (or {class}`RestView <fastapi_restly.views.RestView>` with
{class}`ReactAdminView <fastapi_restly.views.ReactAdminView>` for sync sessions). The react-admin frontend runs on its own
origin during development, so the app also needs the CORS middleware from the
start:

```python
from contextlib import asynccontextmanager

import fastapi_restly as fr
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Mapped

fr.configure(async_database_url="sqlite+aiosqlite:///app.db")

class Product(fr.IDBase):
    name: Mapped[str]
    price: Mapped[float]

@asynccontextmanager
async def lifespan(_app: FastAPI):
    await fr.db.async_create_all(fr.IDBase)  # dev tables; use Alembic in production
    yield

app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # the react-admin dev server
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Range"],
)

@fr.include_view(app)
class ProductView(fr.AsyncReactAdminView):
    prefix = "/products"
    model = Product
```

On the frontend, point `ra-data-simple-rest` at your API:

```jsx
import { Admin, Resource, ListGuesser } from "react-admin";
import simpleRestProvider from "ra-data-simple-rest";

const dataProvider = simpleRestProvider("http://localhost:8000");

export default () => (
  <Admin dataProvider={dataProvider}>
    <Resource name="products" list={ListGuesser} />
  </Admin>
);
```

`ListGuesser` infers the list columns from the response; replace it with your
own list component as the UI takes shape. The [shop example](examples.md#shop)
is a complete runnable version of this setup, with three resources,
relationships, and a react-admin frontend under `ui/test-admin/`.

## Wire contract

{class}`AsyncReactAdminView <fastapi_restly.views.AsyncReactAdminView>` translates the `ra-data-simple-rest` query format to SQL
and returns responses the provider expects.

### List: `GET /resource`

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

`ra-data-simple-rest` requests the path without a trailing slash
(`GET /products?sort=...`). Restly registers the collection routes (list and
create) both with and without the trailing slash, so these requests are
served directly, with no redirect involved.

The `id` array form of `filter` (`{"id": [1, 2, 3]}`) is used by react-admin
for `getMany` calls. It translates to `WHERE id IN (1, 2, 3)`. Other filter
values match by exact equality, and only fields exposed on the response
schema are accepted: unknown fields, including react-admin's full-text `q`
search parameter, are rejected with a 400. Substring or full-text search
requires overriding the view's `apply_query_params`.

### Other operations

Four of the remaining routes serve react-admin's other data provider calls;
the standard PATCH route stays available alongside them for clients that
prefer partial updates:

| Method | Path | Purpose | Source |
|---|---|---|---|
| `GET` | `/{id}` | Get one (react-admin `getOne`) | inherited from {class}`AsyncRestView <fastapi_restly.views.AsyncRestView>` |
| `POST` | `/` | Create (react-admin `create`) | inherited from `AsyncRestView` |
| `PUT` | `/{id}` | Full update (react-admin `update`) | added by {class}`AsyncReactAdminView <fastapi_restly.views.AsyncReactAdminView>` |
| `PATCH` | `/{id}` | Partial update | inherited from `AsyncRestView` |
| `DELETE` | `/{id}` | Delete (react-admin `delete`) | inherited from `AsyncRestView` |

`AsyncReactAdminView` and {class}`ReactAdminView <fastapi_restly.views.ReactAdminView>` add a `PUT /{id}` endpoint because
`ra-data-simple-rest`'s default `update` method issues a `PUT` request. Note
that react-admin sends the complete record on update, `id` and read-only
fields included; the update schema drops the fields it does not accept
instead of rejecting the request.

The PUT route delegates to the same {meth}`handle_update <fastapi_restly.views.RestView.handle_update>` request handler as PATCH and
accepts the view's standard {attr}`schema_update <fastapi_restly.views.BaseRestView.schema_update>` payload. If you need different
write semantics for the two methods, override the {meth}`update <fastapi_restly.views.RestView.update>` business verb (or
`handle_update`, or replace the PUT route directly);
[Customize RestView](customize.md) explains how
these override points relate.

## Serialize related lists as scalar id arrays with `IDRef`

`ra-data-simple-rest` expects to-many references as plain id arrays
(`"products": [1, 2, 3]`). Declare the field as `list[fr.IDRef[Product]]`;
[Lists of References](howto_relationship_idschema.md#lists-of-references)
describes the full behavior. The [shop example](examples.md#shop) runs this
end to end.

## CORS setup

During development the react-admin app and the API run on different origins,
so the browser applies CORS to every request. Two things must be true: the
API must allow the frontend origin, and the `Content-Range` header must be
exposed to JavaScript, because browsers hide non-safelisted response headers
from scripts. Both are handled by adding `CORSMiddleware` with `Content-Range`
in `expose_headers`:

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
on its list responses, so a missing `expose_headers` entry alone does not
break pagination. Do not rely on that fallback: middleware or a proxy that
sets its own `Access-Control-Expose-Headers` replaces the view's header, and
one that strips it hides the total from the frontend entirely. Declare the
exposure in configuration you control.

## Troubleshooting a blank list

A react-admin list that renders "No Products found" against an API that
demonstrably has rows is usually one of three misconfigurations, and the
browser console says which one. Open the devtools console and match the
error:

**"The Content-Range header is missing in the HTTP Response"**, raised by
`ra-data-simple-rest`, means the response reached the browser but JavaScript
cannot read the `Content-Range` header. Add `Content-Range` to
`expose_headers` as shown [above](#cors-setup), and check for middleware or
proxies that overwrite or strip `Access-Control-Expose-Headers`. Seeing the
header in `curl -i` output proves nothing here; what matters is what the
browser exposes to scripts.

**"TypeError: Failed to fetch"** with a CORS policy error above it means the
request itself was blocked because the API sends no
`Access-Control-Allow-Origin` for the frontend origin. Add `CORSMiddleware`
with the frontend origin in `allow_origins`.

**"422 (Unprocessable Content)"** on the list request means the endpoint
rejected react-admin's query grammar, so the view is a plain
{class}`AsyncRestView <fastapi_restly.views.AsyncRestView>`: the standard dialect does not accept the JSON-encoded
`sort`, `range`, and `filter` parameters. Switch the view to
{class}`AsyncReactAdminView <fastapi_restly.views.AsyncReactAdminView>`.

## Customization

A few view-level settings adjust the defaults described above, and a base
class can share them across views.

### Set the default page size

When the frontend does not send a `range` query parameter, the react-admin
views return the first 25 rows, overriding the framework-wide
{attr}`default_page_size <fastapi_restly.views.BaseRestView.default_page_size>` default of `None` (no implicit cap). Set the
attribute on the view to choose a different value:

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
[route replacement](customize.md#replace-an-endpoint-method-to-change-the-http-contract)
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
